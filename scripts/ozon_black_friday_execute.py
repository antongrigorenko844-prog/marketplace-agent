#!/usr/bin/env python3
"""Execute only the four Black Friday prices explicitly approved on 27 November.

Uses the existing exact-request transport, fresh reader and durable executor.
The previous 96-item decision and its operation journal remain independent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ozon_black_friday_pilot import (
    DEFAULT_PLAN, evaluate_item, load_approved_plan, scope_digest,
)
from scripts.ozon_promo_execute import (
    EXECUTION, ExecutionError, Executor, GuardedWriter, Journal, LiveReader,
    OfficialContract, ReadOnlyOzonClient, atomic_json, canonical, load_bundle,
    safe_error, verified_record, digest, plan_core,
)

PILOT = DEFAULT_PLAN.parent


class PilotWriter(GuardedWriter):
    """Persist runtime readbacks through this decision's exact validator."""

    def note_verified(self, item, observation, *, verified=True):
        persisted = load_approved_plan(self.plan_path)
        if (persisted.get("enabled") is not True
                or persisted["decision_id"] != self.plan["decision_id"]
                or persisted["scope_sha256"] != self.plan["scope_sha256"]):
            raise ExecutionError("APPROVAL_CHANGED_DURING_EXECUTION")
        found = [row for row in persisted["items"] if row["sku"] == item["sku"]
                 and str(row["product_id"]) == str(item["product_id"])]
        if len(found) != 1:
            raise ExecutionError("VERIFIED_STATE_IDENTITY_MISMATCH")
        record = verified_record(item, observation, persisted["decision_id"])
        record["verified"] = verified
        found[0]["last_verified"] = record
        if scope_digest(persisted) != self.plan["scope_sha256"]:
            raise ExecutionError("VERIFIED_STATE_CHANGED_APPROVAL_SCOPE")
        atomic_json(self.plan_path, persisted)
        if verified:
            self.plan = persisted


def verify_previous_scope(plan):
    """An amendment cannot hide unresolved operations in the original journal."""
    from ozon_promo_batch import load_approved_plan as load_original
    original = load_original(EXECUTION / "approved_plan.json")
    if original["scope_sha256"] != plan["source"]["superseded_96_scope_sha256"]:
        raise ExecutionError("PREVIOUS_APPROVAL_SCOPE_CHANGED")
    old_by_sku = {row["sku"]: row for row in original["items"]}
    selected = {row["sku"] for row in plan["items"]}
    for row in plan["items"]:
        old = old_by_sku.get(row["sku"])
        if old is None or old["product_id"] != row["product_id"]:
            raise ExecutionError("PREVIOUS_APPROVAL_IDENTITY_MISMATCH")
    path = EXECUTION / "execution_journal.json"
    if not path.is_file():
        raise ExecutionError("PREVIOUS_OPERATION_JOURNAL_REQUIRED")
    previous = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(previous, dict) or previous.get("schema_version") != 1
            or previous.get("decision_id") != original["decision_id"]
            or previous.get("approved_core_sha256") != digest(plan_core(original))
            or not isinstance(previous.get("operations"), list)):
        raise ExecutionError("PREVIOUS_OPERATION_JOURNAL_INVALID")
    for operation in previous["operations"]:
        if (not isinstance(operation, dict) or operation.get("sku") not in old_by_sku
                or not isinstance(operation.get("operation_id"), str)
                or not isinstance(operation.get("state"), str)):
            raise ExecutionError("PREVIOUS_OPERATION_RECORD_INVALID")
    pending = [op for op in previous["operations"]
               if op.get("sku") in selected and op.get("state") != "VERIFIED_BY_READBACK"]
    if pending:
        raise ExecutionError("PREVIOUS_DECISION_HAS_UNRESOLVED_SELECTED_OPERATION")
    return {"decision_id": original["decision_id"], "scope_sha256": original["scope_sha256"],
            "selected_unresolved_operations": 0,
            "previous_journal_operations": len(previous["operations"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=EXECUTION / "official_contract.json")
    parser.add_argument("--journal", type=Path, default=PILOT / "execution_journal.json")
    parser.add_argument("--report", type=Path, default=PILOT / "execution_report.json")
    parser.add_argument("--service-evidence", type=Path, default=PILOT / "service_evidence.json")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    journal = None
    try:
        plan = load_approved_plan(args.plan)
        previous = verify_previous_scope(plan)
        bundle = load_bundle(args.snapshot, args.catalogue)
        contract = OfficialContract(json.loads(args.contract.read_text(encoding="utf-8")))
        evidence = json.loads(args.service_evidence.read_text(encoding="utf-8"))["evidence_by_sku"]
        if set(evidence) - {item["sku"] for item in plan["items"]}:
            raise ExecutionError("SERVICE_EVIDENCE_OUTSIDE_APPROVED_PILOT")
        journal = Journal(args.journal, plan)
        reader = writer = None
        if args.execute:
            from config import config
            client = ReadOnlyOzonClient(config.ozon_client_id, config.ozon_api_key,
                                       base_url=config.ozon_api_base, timeout=config.request_timeout_seconds)
            reader, writer = LiveReader(client), PilotWriter(plan, args.plan)
        executor = Executor(plan, bundle, contract, journal, reader, writer,
                            evaluator=evaluate_item, service_evidence=evidence)
        report = executor.run(execute=args.execute)
        report["previous_scope_check"] = previous
        report["source_snapshot_at"] = bundle["manifest"]["completed_at"]
        report["scope_count"] = 4
        report["approved_action_ids"] = [4532787]
        atomic_json(args.report, report)
        print(canonical({key: report[key] for key in
                         ("decision_id", "mode", "counts", "marketplace_write_attempts")}))
        return 2 if any(row["status"].startswith("HOLD") for row in report["items"]) else 0
    except Exception as exc:
        failure = {"status": "FAILED", "error": safe_error(exc)}
        atomic_json(args.report, failure)
        print(canonical(failure))
        return 1
    finally:
        if journal is not None:
            journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
