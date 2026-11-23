"""Reusable, seller-price-only Avito batch engine.

Safeguards:
* Explicit per-ID owner approval and original expected seller price.
* No writes in inspect mode; cannot infer prices from marketplace prices.
* Throttled concurrent POSTs (default 4 threads, max 100 POST/min).
* No blind retries after ambiguous network errors; live readback determines state.
* Idempotent retries: already-correct listings are skipped.
* No feed, price master, site, stock, images or non-target ID changes.
* A call to this module does NOT publish a source XLSX. The caller must perform
  its own separate verified source / channel-isolation preflight beforehand.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import Lock
from time import monotonic, sleep
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Any

MAX_WORKERS = 4
MAX_REQUESTS_PER_MIN = 100
MAX_PRICE_RUB = 500_000


class AvitoPriceGuardError(ValueError):
    """A safety check failed before or during batch apply."""


@dataclass(frozen=True)
class PriceEdit:
    avito_id: int
    expected_price: int
    approved_price: int
    expected_title: Optional[str] = None
    sku: Optional[str] = None

    def validate(self) -> None:
        if type(self.avito_id) is not int or self.avito_id <= 0:
            raise AvitoPriceGuardError("Avito ID must be a positive integer")
        for label, price in (("expected", self.expected_price),
                             ("approved", self.approved_price)):
            if type(price) is not int or not (1 <= price <= MAX_PRICE_RUB):
                raise AvitoPriceGuardError(
                    f"{self.avito_id}: invalid {label} price {price!r}"
                )
        if self.expected_title is not None and not self.expected_title.strip():
            raise AvitoPriceGuardError(
                f"{self.avito_id}: empty expected title"
            )


class RateGate:
    """Global minimum interval between request starts across all threads.

    100 requests/minute leaves room below the documented 150/min limit.
    The blocking wait occurs OUTSIDE the lock so threads can proceed.
    """

    def __init__(
        self,
        requests_per_minute: int = MAX_REQUESTS_PER_MIN,
        clock: Callable[[], float] = monotonic,
        wait: Callable[[float], None] = sleep,
    ) -> None:
        if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 120:
            raise ValueError("Invalid Avito request rate")
        self._interval = 60.0 / requests_per_minute
        self._clock = clock
        self._wait = wait
        self._lock = Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            due = max(now, self._next)
            self._next = due + self._interval
        delay = due - now
        if delay > 0:
            self._wait(delay)


def preflight(
    edits: Sequence[PriceEdit],
    live: Mapping[int, Mapping[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """Read-only, exact-match validation; refuse missing, inactive or drifted IDs."""
    if not edits:
        raise AvitoPriceGuardError("Empty owner price approval")
    plan: Dict[int, Dict[str, Any]] = {}
    for edit in edits:
        edit.validate()
        if edit.avito_id in plan:
            raise AvitoPriceGuardError(f"Duplicate Avito ID {edit.avito_id}")
        ad = live.get(edit.avito_id)
        if ad is None or ad.get("status") != "active":
            raise AvitoPriceGuardError(f"{edit.avito_id}: not active in seller API")
        actual = ad.get("price")
        if type(actual) not in (int, float) or int(actual) != actual:
            raise AvitoPriceGuardError(f"{edit.avito_id}: unexpected live price format")
        actual = int(actual)
        if actual not in (edit.expected_price, edit.approved_price):
            raise AvitoPriceGuardError(
                f"{edit.avito_id}: live price {actual} not equal to "
                f"owner baseline {edit.expected_price} or {edit.approved_price}"
            )
        if edit.expected_title is not None and ad.get("title") != edit.expected_title:
            raise AvitoPriceGuardError(f"{edit.avito_id}: title changed externally")
        plan[edit.avito_id] = {
            "before": actual,
            "expected_price": edit.expected_price,
            "approved_price": edit.approved_price,
            "sku": edit.sku,
            "status": "already_correct" if actual == edit.approved_price else "pending",
        }
    return plan


def assert_site_prices_unchanged(
    before: Mapping[str, float],
    after: Mapping[str, float],
    affected_skus: Sequence[str],
) -> None:
    """Call BEFORE publishing feed or seller changes. Include site-only JSON."""
    for sku in affected_skus:
        previous = before.get(sku)
        proposed = after.get(sku)
        if previous is None or proposed is None:
            raise AvitoPriceGuardError(f"Site effective price unknown for {sku}")
        if float(previous) != float(proposed):
            raise AvitoPriceGuardError(
                f"Site price drift for {sku}: {previous} -> {proposed}"
            )


def apply(
    edits: Sequence[PriceEdit],
    original_live: Mapping[int, Mapping[str, Any]],
    *,
    owner_approved: bool,
    mode: str,
    update_fn: Callable[[int, int], Mapping[str, Any]],
    read_live_fn: Callable[[], Mapping[int, Mapping[str, Any]]],
    checkpoint: Optional[Callable[[Mapping[str, Any]], None]] = None,
    workers: int = MAX_WORKERS,
    rate_gate: Optional[RateGate] = None,
) -> Dict[str, Any]:
    """Publish *only* directly approved seller prices; one seller list readback.

    Does not publish XLSX or update master source. Caller must first validate
    exact effective site prices and publish its independently guarded feed.
    """
    if mode not in ("inspect", "apply"):
        raise AvitoPriceGuardError(f"Invalid mode {mode!r}")
    plan = preflight(edits, original_live)
    if mode == "apply" and owner_approved is not True:
        raise AvitoPriceGuardError("Explicit owner_approved=true is required")
    if not (1 <= workers <= MAX_WORKERS):
        raise AvitoPriceGuardError("Too many simultaneous seller writes")

    summary: Dict[str, Any] = {
        "mode": mode,
        "planned": len(plan),
        "already_correct": sum(v["status"] == "already_correct" for v in plan.values()),
        "sent": 0,
        "accepted": 0,
        "verified": 0,
        "unverified": len(plan),
        "details": {str(k): dict(v) for k, v in plan.items()},
    }
    if mode == "inspect":
        summary["status"] = "inspect_passed_no_writes"
        if checkpoint:
            checkpoint(summary)
        return summary

    gate = rate_gate or RateGate()

    # Warm up OAuth in calling code, before entering this function if the
    # token cache is shared by multiple threads. This worker never reads or
    # writes unrelated seller IDs. Errors are reconciled by live readback.
    pending = [
        (ident, row["approved_price"])
        for ident, row in plan.items()
        if row["status"] != "already_correct"
    ]

    def work(entry):
        ident, price = entry
        gate.acquire()
        try:
            response = update_fn(ident, price)
            ok = (response.get("result") or {}).get("success") is True
            return ident, "accepted" if ok else "unconfirmed", None
        except Exception as exc:
            # Do not repeat potentially successful POST on a timeout.
            return ident, "uncertain", type(exc).__name__

    if pending:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(work, pair): pair[0] for pair in pending}
            for future in as_completed(futures):
                ident, status, error = future.result()
                detail = summary["details"][str(ident)]
                detail["status"] = status
                if error is not None:
                    detail["error_type"] = error
                summary["sent"] += 1
                summary["accepted"] += int(status == "accepted")
                if checkpoint:
                    checkpoint(summary)

    # Exactly one list readback for an entire batch, never N reads for N IDs.
    fresh = read_live_fn()
    for ident, detail in summary["details"].items():
        ad = fresh.get(int(ident), {})
        actual = ad.get("price")
        if type(actual) in (int, float) and int(actual) == actual:
            actual = int(actual)
        else:
            actual = None
        ok = ad.get("status") == "active" and actual == detail["approved_price"]
        detail["actual_price_after"] = actual
        detail["status_after"] = ad.get("status", "missing")
        detail["verified"] = ok
        summary["verified"] += int(ok)
    summary["unverified"] = len(plan) - summary["verified"]
    summary["status"] = (
        "verified_all_prices" if summary["unverified"] == 0
        else "partial_requires_review"
    )
    if checkpoint:
        checkpoint(summary)
    return summary
