"""LEOPARTS Product Publishing v3 — guarded, resumable NEW-SKU publisher.

One manifest, one workflow, one journal. Invariant: never update a card
existing before this batch; never infer photos, categories, customs or prices.
Marketplace seller writes are DISABLED unless an exact manifest checksum and
explicit owner authorization are present in dispatch.json.

Each external POST/PUT is preceded by an atomically Git-persisted intent.
An interrupted operation is never blindly retried. Recovery first performs
a seller read; uncertain outcomes stay blocked instead of creating duplicates.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
from threading import RLock, Lock
import time
from typing import Any

from scripts import product_intake_v2 as intake
from scripts import product_publishing_v3_adapters as adapters

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "data" / "product_intake_v3" / "state"
DISPATCH = ROOT / "data" / "product_intake_v3" / "dispatch.json"
ACTIVE = ("ozon", "wildberries", "yandex_market", "tilda")
STOCK_COL = 8
MASTER_STOCK_COL = 10
MEDIA_LOCK = Lock()


class PublicationGuard(ValueError):
    pass


def require(condition: bool, message: str):
    if not condition:
        raise PublicationGuard(message)


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def fingerprint(batch: dict) -> str:
    intake.validate(batch)
    canonical = json.dumps(batch, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verified_authorization(batch: dict, config: dict, *, require_apply: bool):
    require(config.get("schema_version") == 1, "Unrecognized dispatch schema")
    require(config.get("batch_id") == batch["batch_id"], "Approved batch differs from dispatch")
    channels = config.get("channels")
    require(isinstance(channels, list) and channels and len(channels) == len(set(channels)),
            "Invalid channel selection")
    require(all(c in ("ozon", "wildberries", "yandex_market", "tilda", "avito") for c in channels),
            "Unsupported channel")
    if require_apply:
        require(config.get("mode") == "apply", "Mode apply must be explicit")
        require(config.get("apply_authorized") is True and
                config.get("allow_existing_changes") is False,
                "Exact owner approval required; existing product changes forbidden")
        require(config.get("approved_manifest_sha256") == fingerprint(batch),
                "Manifest changed after approval: refuse ALL seller writes")
        require(batch["policy"].get("never_recalculate_approved_prices") is True,
                "Automatic repricing safety guard must be enabled")
        require(batch["policy"].get("no_duplicate_create") is True,
                "No-duplicates guard missing")
    return tuple(c for c in channels if c in ACTIVE)


def catalog_stock(sku: str) -> int:
    """Operational quantity is solely the exact current catalog + master.

    Never fill missing inventory using the months-old approved batch's stock.
    The stock-sync job owns existing rows and order reconciliation.
    """
    from openpyxl import load_workbook
    results = []
    for path, col, sheetname in (
        (ROOT / "data/ozon_catalog.xlsx", STOCK_COL, None),
        (ROOT / "data/master_control.xlsx", MASTER_STOCK_COL, "Товары"),
    ):
        require(path.is_file(), "Canonical inventory file absent: " + path.name)
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb[sheetname] if sheetname in wb.sheetnames else wb.active
            rows = [row for row in ws.iter_rows(min_row=2, values_only=True)
                    if row[0] is not None and str(row[0]).strip().casefold() == sku.casefold()]
            require(len(rows) == 1 and str(rows[0][0]).strip() == sku,
                    "SKU missing/ambiguous in canonical inventory: " + sku + "/" + path.name)
            raw = rows[0][col - 1] if len(rows[0]) >= col else None
            require(type(raw) in (int, float) and int(raw) == raw and raw >= 0,
                    "Invalid operational stock: " + path.name + "/" + sku)
            results.append(int(raw))
        finally:
            wb.close()
    require(results[0] == results[1],
            "Canonical catalog and master stock mismatch: " + sku)
    return results[0]


def verified_media(item: dict) -> tuple[list[str], list[Path]]:
    """Never substitute a similar product photo or an unverified URL."""
    paths, blockers = intake.media_preflight(item)
    require(not blockers and len(paths) == len(item["media"]),
            "Approved media hash preflight failed: " + ",".join(blockers))
    urls: list[str] = []
    for i, (entry, path) in enumerate(zip(item["media"], paths), start=1):
        if entry["source"] == "public_url":
            urls.append(entry["url"])
            continue
        require(entry["source"] == "repository", "Missing Library-to-repository binary bridge")
        import photo_host
        # Immutable object name keyed by the full source SHA. Do not reuse an
        # existing SKU's image URL; identical owner-approved bytes dedupe.
        name = f"v3-{item['sku']}-{i}-{entry['sha256'][:16]}{path.suffix.lower()}"
        with MEDIA_LOCK:
            urls.append(photo_host.upload_file(str(path), name))
    require(len(urls) == len(item["media"]), "Media staging incomplete")
    return urls, paths


class Journal:
    def __init__(self, batch: dict, path: Path, *, persist_git: bool = False):
        self.path = path
        self.lock = RLock()
        self.persist_git = persist_git
        self.sha = fingerprint(batch)
        self.batch_id = batch["batch_id"]
        if self.path.exists():
            data = intake.read_json(self.path)
            require(data.get("batch_id") == self.batch_id and
                    data.get("manifest_sha256") == self.sha,
                    "Durable state belongs to a different manifest revision")
            require(data.get("schema_version") == 1 and type(data.get("tasks")) is dict,
                    "Invalid durable journal schema")
            self.data = data
        else:
            self.data = {"schema_version": 1, "batch_id": self.batch_id,
                         "manifest_sha256": self.sha, "tasks": {}, "created_at": timestamp()}
        self.data["last_checked_at"] = timestamp()

    def get(self, sku: str, channel: str) -> dict:
        with self.lock:
            return deepcopy(self.data["tasks"].get(sku + "::" + channel, {}))

    def put(self, sku: str, channel: str, **updates) -> dict:
        with self.lock:
            key = sku + "::" + channel
            row = self.data["tasks"].setdefault(key, {"sku": sku, "channel": channel})
            row.update(updates)
            row["updated_at"] = timestamp()
            self._save()
            return deepcopy(row)

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        draft = self.path.with_name(self.path.name + ".tmp")
        draft.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
        draft.replace(self.path)
        if not self.persist_git:
            return
        require(os.getenv("GITHUB_ACTIONS") == "true",
                "Git persistence permitted only on the GitHub runner")
        rel = str(self.path.relative_to(ROOT))
        subprocess.run(["bash", ".github/scripts/save_data.sh", rel], cwd=ROOT,
                       check=True, timeout=120, stdout=subprocess.DEVNULL)
        # If Git reconciles concurrent commits by discarding our state,
        # REFUSE the seller write, never continue without a durable intent.
        persisted = intake.read_json(self.path)
        require(persisted == self.data, "Git commit did not persist the exact operation journal")


class Publisher:
    def __init__(self, manifest: dict, dispatch: dict, *,
                 mode: str = "pilot", adapter_map: dict | None = None,
                 state_path: Path | None = None, git_checkpoint: bool = False):
        self.batch = intake.validate(manifest)
        self.mode = mode
        require(mode in ("pilot", "apply", "resume"), "Unknown writer mode")
        self.channels = verified_authorization(manifest, dispatch, require_apply=mode != "pilot")
        if mode == "resume":
            require(dispatch.get("mode") == "apply", "Resume requires signed apply dispatch")
        self.manifest_by_sku = {x["sku"]: x for x in manifest["items"]}
        self.adapter_map = adapter_map or {}
        self.write = (mode != "pilot")
        # Live API moderation commonly settles quickly. A bounded short poll
        # avoids making the owner wait for the next 30-minute scheduled retry.
        # Mocked suites deliberately do not sleep.
        self.poll_delays = () if adapter_map is not None else (8, 15)
        self.journal = Journal(manifest, state_path or (STATE_DIR / (manifest["batch_id"] + ".json")),
                               persist_git=git_checkpoint)
        require(not self.write or git_checkpoint or adapter_map is not None,
                "Real seller writes REQUIRE durable Git journal checkpointing")
        self.events: list[dict] = []
        self.events_lock = Lock()

    def event(self, sku: str, channel: str, state: str, *, detail: str = ""):
        with self.events_lock:
            self.events.append({"sku": sku, "channel": channel, "state": state,
                                "detail": detail[:200], "time": timestamp()})

    def record(self, sku, channel, **updates):
        if self.write:
            return self.journal.put(sku, channel, **updates)
        return dict(self.journal.get(sku, channel), **updates)

    def _prepared(self, channel: str, item: dict, adapter) -> tuple[dict, list[str]]:
        sku = item["sku"]
        # No new SKU may become saleable unless the canonical shared stock
        # ledger already has an exactly matching, nonnegative quantity.
        stock = catalog_stock(sku)
        require(stock <= item["stock"], "New product stock exceeds last owner-approved quantity")
        urls, paths = verified_media(item)
        payload = adapter.prepare(item, urls, paths, stock=stock)
        return payload, urls

    def _safe_side_effect(self, adapter, item: dict, channel: str, phase: str, action, *args) -> dict:
        sku = item["sku"]
        row = self.journal.get(sku, channel)
        # Unknown result cannot be retried — another party may have changed
        # a price or sold a unit while the write request was in flight.
        if row.get(phase + "_intent") and not row.get(phase + "_ack"):
            self.record(sku, channel, state="needs_review",
                        blocker="Unknown " + phase + " outcome; only human/confirmed API readback can clear")
            self.event(sku, channel, "uncertain_" + phase, detail="Never blindly repeat unknown POST/PUT")
            return {"state": "uncertain_" + phase}
        if row.get(phase + "_ack"):
            return {"state": "already_" + phase, "ack": row.get(phase + "_ack")}
        self.record(sku, channel, **{phase + "_intent": {"at": timestamp()}})
        try:
            ack = action(*args)
        except Exception as exc:
            self.record(sku, channel, state="needs_review",
                        blocker=type(exc).__name__ + ": " + str(exc)[:180])
            self.event(sku, channel, "uncertain_" + phase,
                       detail=type(exc).__name__ + ": " + str(exc)[:155])
            return {"state": "uncertain_" + phase}
        self.record(sku, channel, **{phase + "_ack": ack})
        return {"state": "ack_" + phase, "ack": ack}

    def _final_readback(self, channel: str, item: dict) -> dict:
        """Independent readback, never accept only a successful HTTP upload."""
        from scripts import product_publishing_v3 as reader
        readers = {
            "ozon": reader.verify_ozon,
            "wildberries": reader.verify_wildberries,
            "yandex_market": reader.verify_yandex_market,
            "tilda": reader.verify_tilda,
        }
        observed, _ = readers[channel]([item])
        result = observed.get(item["sku"]) or {"state": "not_found"}
        if channel == "ozon":
            verified = result.get("state") == "selling"
        elif channel == "wildberries":
            verified = result.get("state") == "catalog_verified"
        elif channel == "yandex_market":
            verified = result.get("state") == "business_catalog_verified"
        else:
            verified = result.get("state") == "public_catalog_verified"
        return {"verified": verified, "observed": result,
                "scope": "selling" if channel == "ozon" and verified
                         else "catalog_and_price_only" if verified else "not_verified"}

    def _process_one(self, channel: str, adapter, item: dict) -> None:
        sku = item["sku"]
        row = self.journal.get(sku, channel)
        try:
            matching = adapter.existing(sku)
            require(len(matching) <= 1, "Seller has duplicate exact/casefold SKU")
        except Exception as exc:
            self.event(sku, channel, "read_error", detail=str(exc))
            return
        if matching and not row.get("create_intent"):
            self.event(sku, channel, "existing_untouched",
                       detail="Already on seller; no modifications allowed")
            return
        if row.get("state") in ("blocked_moderation", "done", "needs_review"):
            self.event(sku, channel, row["state"])
            return

        if not matching and not row.get("create_intent"):
            if self.mode == "resume":
                self.event(sku, channel, "not_submitted")
                return
            # A second seller scan immediately before creation protects
            # against an old one-off workflow that created the SKU during
            # image/category preflight in another process.
            try:
                payload, urls = self._prepared(channel, item, adapter)
            except Exception as exc:
                self.event(sku, channel, "blocked_preflight", detail=str(exc))
                return
            if not self.write:
                self.event(sku, channel, "ready_to_create")
                return
            try:
                adapter.scan()
                current = adapter.existing(sku)
                require(len(current) <= 1, "Duplicate SKU appeared before submit")
                if current:
                    self.event(sku, channel, "existing_untouched",
                               detail="Seller card created concurrently; do not send duplicate")
                    return
            except Exception as exc:
                self.event(sku, channel, "read_error_before_submit", detail=str(exc))
                return
            # Commit the exact immutable intent BEFORE touching the seller API.
            self.record(sku, channel, state="create_intent", create_intent={
                "at": timestamp(), "manifest_sha256": self.journal.sha,
                "approved_price": item["prices"][channel],
                "source_stock": catalog_stock(sku),
                "media_sha256": [m["sha256"] for m in item["media"]]})
            try:
                ack = adapter.submit(item, payload)
            except Exception as exc:
                self.event(sku, channel, "create_outcome_unknown",
                           detail=type(exc).__name__ + ": " + str(exc)[:155])
                return
            self.record(sku, channel, state="create_submitted", create_ack=ack)
            row = self.journal.get(sku, channel)
        if not self.write:
            self.event(sku, channel, "exists_pilot_readonly" if matching else "ready_to_create")
            return
        # Reconcile after a previous submission or crash. Never send create
        # a second time. Ozon import task_id and SKU are read-only here.
        try:
            observed = adapter.observe(item, row.get("create_ack") or {})
        except Exception as exc:
            self.event(sku, channel, "read_error_after_submit", detail=str(exc))
            return
        self.record(sku, channel, last_observation=observed)
        state = observed.get("state")
        if state == "rejected":
            self.record(sku, channel, state="blocked_moderation")
            self.event(sku, channel, "blocked_moderation", detail=str(observed.get("errors"))[:170])
            return
        if state in ("not_created", "moderating"):
            for delay in self.poll_delays:
                time.sleep(delay)
                try:
                    observed = adapter.observe(item, row.get("create_ack") or {})
                except Exception:
                    # Never retry the POST, only the safe readback.
                    continue
                self.record(sku, channel, last_observation=observed)
                state = observed.get("state")
                if state not in ("not_created", "moderating"):
                    break
            if state in ("not_created", "moderating"):
                self.record(sku, channel, state="awaiting_platform")
                self.event(sku, channel, "awaiting_platform")
                return
        if state not in ("created", "selling"):
            self.event(sku, channel, "unknown_platform_state", detail=str(state))
            return
        # Ownership proof: create_intent from this exact manifest, plus newly
        # created seller row. Existing prelaunch cards never reach this branch.
        row = self.journal.get(sku, channel)
        if not row.get("create_intent"):
            self.event(sku, channel, "not_owned_legacy_card")
            return
        try:
            stock = catalog_stock(sku)
            require(stock <= item["stock"], "Operational stock exceeds owner-approved target")
            # If customer orders have reduced stock since creation, use the
            # latest canonical quantity (never stale request qty).
        except Exception as exc:
            self.event(sku, channel, "blocked_stock_source", detail=str(exc))
            return
        if not row.get("create_ack"):
            # The POST may have timed out; finding the SKU later proves
            # existence, NOT that this batch created it. Never assume
            # ownership of a card without the original create acknowledgement.
            self.record(sku, channel, state="needs_review",
                        blocker="Seller SKU exists but initial create acknowledgement was lost")
            self.event(sku, channel, "uncertain_created_identity")
            return
        if channel == "wildberries":
            # Photos and prices are separate asynchronous WB tasks.
            # For recovered tasks, approved media URLs must be revalidated.
            try:
                urls, _ = verified_media(item)
            except Exception as exc:
                self.event(sku, channel, "blocked_media", detail=str(exc))
                return
            for phase, action, args in (
                ("media", adapter.set_media, (item, row.get("create_ack") or {}, urls)),
                ("price", adapter.set_price, (item, row.get("create_ack") or {})),
            ):
                status = self._safe_side_effect(adapter, item, channel, phase, action, *args)
                if status["state"].startswith("uncertain"):
                    return
        elif channel == "yandex_market":
            status = self._safe_side_effect(adapter, item, channel, "price",
                                            adapter.set_price, item, row.get("create_ack") or {})
            if status["state"].startswith("uncertain"):
                return
        if channel != "tilda":
            status = self._safe_side_effect(adapter, item, channel, "stock",
                                            adapter.set_stock, item, row.get("create_ack") or {}, stock)
            if status["state"].startswith("uncertain"):
                return
        self.record(sku, channel, state="configured_pending_final_readback")
        try:
            result = self._final_readback(channel, item)
            self.record(sku, channel, final_readback=result)
            if result["verified"]:
                self.record(sku, channel, state="done")
                self.event(sku, channel, "done",
                           detail="Verified " + result["scope"] + " via independent API readback")
            else:
                self.event(sku, channel, "configured_pending_final_readback",
                           detail="Await storefront/price/stock API confirmation")
        except Exception as exc:
            # Observability failure must not lead to another create/price/stock.
            self.event(sku, channel, "configured_pending_final_readback",
                       detail=type(exc).__name__ + ": " + str(exc)[:155])

    def _channel(self, channel: str):
        adapter = self.adapter_map.get(channel) or adapters.factory(channel)
        selected = [item for item in self.batch["items"]
                    if channel in item.get("channels", list(ACTIVE))]
        if not selected:
            return
        try:
            adapter.scan()
        except Exception as exc:
            for item in selected:
                self.event(item["sku"], channel, "api_scan_error",
                           detail=type(exc).__name__ + ": " + str(exc)[:150])
            return
        for item in selected:
            try:
                self._process_one(channel, adapter, item)
            except Exception as exc:
                self.event(item["sku"], channel, "blocked_error",
                           detail=type(exc).__name__ + ": " + str(exc)[:150])

    def run(self) -> dict:
        # Each adapter serializes its own requests; independent channels can
        # progress in parallel with one thread per platform.
        with ThreadPoolExecutor(max_workers=max(1, len(self.channels))) as pool:
            jobs = [pool.submit(self._channel, channel) for channel in self.channels]
            for job in as_completed(jobs):
                job.result()
        status = [e["state"] for e in self.events]
        report = {"batch_id": self.batch["batch_id"], "mode": self.mode,
                  "checked_at_utc": timestamp(), "journal_sha256": self.journal.sha,
                  "items": deepcopy(self.journal.data["tasks"]) if self.write else {},
                  "events": self.events,
                  "summary": {"tasks": len(self.events),
                              "existing_untouched": status.count("existing_untouched"),
                              "ready_to_create": status.count("ready_to_create"),
                              "submitted_or_configured": sum(x in ("awaiting_platform",
                                    "configured_pending_final_readback") for x in status),
                              "done": status.count("done"),
                              "blocked_or_uncertain": sum(x.startswith(("blocked", "uncertain",
                                    "read_error", "api_scan_error")) for x in status),
                              "writes_enabled": self.write,
                              "avito": "disabled_by_unpaid_autoload"}}
        return report


def run_dispatch(*, mode: str, state_path: Path | None = None, adapter_map: dict | None = None,
                 git_checkpoint: bool = False) -> dict:
    config = intake.read_json(DISPATCH)
    batch_id = config.get("batch_id")
    require(isinstance(batch_id, str) and intake.BATCH_PATTERN.fullmatch(batch_id),
            "Invalid approved batch ID")
    request = ROOT / "data" / "product_intake_v2" / "requests" / (batch_id + ".json")
    manifest = intake.read_json(request)
    require(mode == "pilot" or config.get("mode") == "apply",
            "Apply/resume needs explicit dispatch.apply")
    publisher = Publisher(manifest, config, mode=mode,
                          state_path=state_path, adapter_map=adapter_map,
                          git_checkpoint=git_checkpoint)
    return publisher.run()


def cli():
    import argparse
    parser = argparse.ArgumentParser(description="LEOPARTS guarded v3 create/resume; existing SKU untouched")
    parser.add_argument("--mode", choices=("pilot", "apply", "resume"), default="pilot")
    parser.add_argument("--git-checkpoint", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_dispatch(mode=args.mode, git_checkpoint=args.git_checkpoint)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        draft = args.output.with_suffix(".tmp")
        draft.write_text(text, encoding="utf-8")
        draft.replace(args.output)
    else:
        print(text)


if __name__ == "__main__":
    cli()
