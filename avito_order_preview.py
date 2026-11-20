"""Aggregate a read-only Avito Delivery preview. No files are written.

Run explicitly with --live. Optional --data-dir points to the repository's data
directory; credentials are read only from AVITO_CLIENT_ID and AVITO_CLIENT_SECRET.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from xml.etree import ElementTree as ET
from zipfile import ZipFile


API = "https://api.avito.ru"
CUT = datetime(2026, 11, 20, 17, 0, 57, tzinfo=timezone.utc)
GROUP = {
    "on_confirmation": "active", "ready_to_ship": "active", "in_transit": "active",
    "delivered": "delivered", "canceled": "canceled", "closed": "closed",
    "on_return": "return", "in_dispute": "dispute",
}
PAGE_SIZE, PAGE_CAP = 20, 50


class Invalid(Exception):
    """Only codes fixed in this script may be exposed."""


def require(condition, code):
    if not condition:
        raise Invalid(code)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def avito_pages():
    client = os.environ.get("AVITO_CLIENT_ID")
    secret = os.environ.get("AVITO_CLIENT_SECRET")
    require(client and secret, "credentials_unavailable")
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def read(request):
        try:
            with opener.open(request, timeout=20) as response:
                require(response.status == 200, "unexpected_http_response")
                require(response.headers.get_content_type() == "application/json", "invalid_content_type")
                raw = response.read(8_000_001)
                require(len(raw) <= 8_000_000, "response_size_limit")
                return json.loads(raw)
        except HTTPError as error:
            error.close()
            raise Invalid("http_" + str(int(error.code))) from None
        except (OSError, URLError):
            raise Invalid("network_error") from None
        except (ValueError, RecursionError):
            raise Invalid("invalid_json") from None

    form = urlencode({"grant_type": "client_credentials", "client_id": client, "client_secret": secret})
    auth = read(Request(API + "/token", method="POST", data=form.encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded"}))
    token = auth.get("access_token") if isinstance(auth, dict) else None
    require(isinstance(token, str) and token.isascii() and token.isprintable()
            and token and not any(c.isspace() for c in token), "invalid_token_response")

    def page(number, start):
        query = urlencode({"dateFrom": int(start.timestamp()), "page": number, "limit": PAGE_SIZE})
        return read(Request(API + "/order-management/1/orders?" + query, method="GET",
                            headers={"Authorization": "Bearer " + token}))

    return page


def listing_id(value):
    value = str(value)
    require(re.fullmatch("[1-9][0-9]*", value), "invalid_listing_id")
    return value


def master_skus(path):
    # Read only SKU column A on the named sheet. Support both Excel string formats.
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    rel = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with ZipFile(path) as book:
        sheets = ET.fromstring(book.read("xl/workbook.xml")).findall(".//" + ns + "sheet")
        target_id, = [s.attrib[rel] for s in sheets if s.get("name") == "Товары"]
        targets = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        target, = [r.attrib["Target"] for r in targets if r.get("Id") == target_id]
        part = target.lstrip("/") if target.startswith("/") else "xl/" + target
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            strings = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in item.iter(ns + "t")) for item in strings]
        cells = {}
        for c in ET.fromstring(book.read(part)).iter(ns + "c"):
            addr = c.get("r", "")
            if re.fullmatch("A[1-9][0-9]*", addr):
                val = c.findtext(ns + "v", "")
                if c.get("t") == "s":
                    val = shared[int(val)]
                elif c.get("t") == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(ns + "t"))
                cells[addr] = val
    require(cells.pop("A1", None) == "Артикул (offer_id)", "catalog_header")
    return Counter(value for value in cells.values() if value)


def exact_map(directory):
    skus = master_skus(directory / "master_control.xlsx")
    links = json.loads((directory / "avito_links.json").read_bytes())
    claims = defaultdict(set)
    for sku, number in links.items():
        if not sku.startswith("_"):
            claims[listing_id(number)].add(sku if skus[sku] == 1 else None)
    rows = json.loads((directory / "avito_autoload.json").read_bytes())["items"]
    require(isinstance(rows, list), "catalog_schema")
    for row in rows:
        if row.get("avito_id") is None:
            continue
        number, ad = listing_id(row["avito_id"]), row["ad_id"]
        require(isinstance(ad, str), "catalog_schema")
        if ad.endswith("-av") and skus[ad] == 0 and skus[ad[:-3]] == 1 and str(links.get(ad[:-3])) == number:
            ad = ad[:-3]
        claims[number].add(ad if skus[ad] == 1 else None)
    return {n: next(iter(v)) for n, v in claims.items() if len(v) == 1 and None not in v}


def timestamp(value):
    require(isinstance(value, str) and "T" in value, "invalid_timestamp")
    try:
        t = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        raise Invalid("invalid_timestamp") from None
    require(t.tzinfo is not None, "invalid_timestamp")
    return t


def normalize(order):
    require(isinstance(order, dict), "invalid_order")
    key, status = order.get("id"), order.get("status")
    if type(key) is int and key > 0:
        key = str(key)
    require(isinstance(key, str) and key.strip() and key == key.strip(), "invalid_order_id")
    require(isinstance(status, str) and status.strip(), "invalid_status")
    created, updated = timestamp(order.get("createdAt")), timestamp(order.get("updatedAt"))
    require(created <= updated, "invalid_timestamp_order")
    items = order.get("items")
    require(isinstance(items, list) and items, "invalid_items")
    counts = Counter()
    for item in items:
        require(isinstance(item, dict), "invalid_item")
        qty = item.get("count")
        require(type(qty) is int and qty > 0, "invalid_quantity")
        counts[listing_id(item.get("avitoId"))] += qty
    return key, (created, updated, status, tuple(sorted(counts.items())))


def metrics():
    return {"orders": 0, "units": 0, "exact_sku_units": 0}


def read_snapshot(fetch, start, end):
    seen, conflicts, reasons = {}, set(), set()
    duplicates = pages = 0
    exhausted = False
    try:
        for number in range(1, PAGE_CAP + 1):
            response = fetch(number, start)
            require(isinstance(response, dict), "invalid_page")
            rows, more = response.get("orders"), response.get("hasMore")
            require(isinstance(rows, list) and type(more) is bool
                    and len(rows) <= PAGE_SIZE and (rows or not more), "invalid_page")
            page_states = [normalize(row) for row in rows]
            require(all(start <= state[0] for _, state in page_states), "outside_date_from")
            for key, state in page_states:
                if state[1] >= end:
                    reasons.add("state_at_or_after_scan_start")
                if key in seen:
                    duplicates += 1
                    if seen[key] != state:
                        conflicts.add(key)
                else:
                    seen[key] = state
            pages += 1
            if number == PAGE_CAP:
                reasons.add("page_cap")
            if not more:
                exhausted = True
                break
    except Invalid as error:
        reasons.add(str(error))
    if duplicates:
        reasons.add("repeated_order_across_or_within_pages")
    if conflicts:
        reasons.add("conflicting_order_versions")
    return seen, conflicts, reasons, duplicates, pages, exhausted


def scan(fetch, start, end, mapping):
    seen, conflicts, reasons, duplicates, pages, exhausted = read_snapshot(fetch, start, end)
    status_counts = dict.fromkeys([*GROUP, "unknown"], 0)
    buckets = {
        period: {group: metrics() for group in dict.fromkeys([*GROUP.values(), "unknown"])}
        for period in ("created_before_checkpoint", "created_at_or_after_checkpoint")
    }
    distinct_skus = set()
    after_end = 0
    for key, (created, _, status, units) in seen.items():
        if key in conflicts:
            continue
        if created >= end:
            # dateFrom has no dateTo partner. New orders can move page boundaries.
            after_end += 1
            reasons.add("creation_at_or_after_scan_start")
            continue
        group = GROUP.get(status, "unknown")
        status_counts[status if status in GROUP else "unknown"] += 1
        period = "created_before_checkpoint" if created < CUT else "created_at_or_after_checkpoint"
        summary = buckets[period][group]
        summary["orders"] += 1
        for item_id, qty in units:
            summary["units"] += qty
            if mapping is not None and item_id in mapping:
                distinct_skus.add(mapping[item_id])
                summary["exact_sku_units"] += qty
    total = metrics()
    for groups in buckets.values():
        for summary in groups.values():
            for key in total:
                total[key] += summary[key]
            if mapping is None:
                summary["exact_sku_units"] = None
    if mapping is None:
        total["exact_sku_units"] = None
    return {
        "created_window_utc": {"from_inclusive": start.isoformat(), "to_exclusive": end.isoformat()},
        "technical_publication_checkpoint_utc": CUT.isoformat(),
        "checkpoint_is_physical_count_time": False,
        "validated_pages": pages, "has_more_false_seen": exhausted,
        "accessible_pagination_passed": exhausted and not reasons,
        "full_history_proven": False,
        "history_limits": ["closed_orders_retention", "mutable_pages_without_snapshot_token"],
        "issues": sorted(reasons), "repeated_rows": duplicates,
        "excluded_conflicting_orders": len(conflicts), "excluded_after_window": after_end,
        "observed_totals": total,
        "current_status_counts": status_counts, "current_lifecycle_by_creation": buckets,
        "all_observed_statuses_classified": status_counts["unknown"] == 0,
        "exact_sku_map_available": mapping is not None,
        "sku_coverage_basis": "current_repository_mapping" if mapping is not None else None,
        "distinct_exact_skus_observed": None if mapping is None else len(distinct_skus),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    end = datetime.now(timezone.utc).replace(microsecond=0)
    # Leave five minutes of margin so subsequent pages stay inside 180 days.
    start = end - timedelta(days=180) + timedelta(minutes=5)
    mapping = None
    mapping_status = "not_requested"
    if args.data_dir:
        try:
            mapping = exact_map(args.data_dir)
            mapping_status = "available"
        except Exception:
            mapping_status = "unavailable_or_invalid"
    report = scan(avito_pages(), start, end, mapping)
    report["local_mapping_status"] = mapping_status
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["accessible_pagination_passed"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Invalid as exc:
        print(json.dumps({"accessible_pagination_passed": False, "full_history_proven": False, "error": str(exc)}))
    except Exception:
        print('{"accessible_pagination_passed":false,"full_history_proven":false,"error":"input_or_runtime_error"}')
    sys.exit(2)
