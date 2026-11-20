"""Publish finite Avito availability for the six pinned LEOPARTS listings."""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import NamedTemporaryFile
import time

from openpyxl import load_workbook

from avito_stock_client import LISTINGS, StockClient, StockError
from kits import load_bom
from stock_transaction import StockSyncError, assert_stock_ready, units


STATE = "avito_stock_state.json"
BOOK, BOM, DB = "ozon_catalog.xlsx", "kits.xlsx", "orders_seen.db"
CONFIRMED_KITS = {"plita-filtr-orig", "plita-pylniki-salniki",
                  "plita-pylniki-salniki-orig", "plita-pylniki-salniki-orig-v2"}
PLAN_LIFETIME = 900


class PolicyError(Exception):
    """Locally defined public diagnostic, optionally with a partial summary."""
    def __init__(self, code, items=None):
        super().__init__(code)
        self.items = items


def require(ok, code):
    if not ok:
        raise PolicyError(code)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def valid_qty(n):
    return type(n) is int and 0 <= n <= 999999


def fingerprint(data):
    db = data / DB
    require(db.is_file(), "missing_stock_ledger")
    for suffix in ("-wal", "-journal"):
        side = Path(str(db) + suffix)
        require(not side.exists() or not side.stat().st_size, "stock_ledger_busy")
    try:
        assert_stock_ready(db)
    except StockSyncError:
        raise PolicyError("stock_projection_pending") from None
    return digest({name: sha256((data/name).read_bytes()).hexdigest() for name in (BOOK,BOM,DB)})


def targets(data):
    signature = fingerprint(data)
    recipes = load_bom(str(data/BOM), strict=True)
    require(CONFIRMED_KITS <= recipes.keys(), "missing_confirmed_kit")
    required = set(LISTINGS)
    for sku in LISTINGS:
        required.update(component for component, _ in recipes.get(sku, ()))
    levels = {}
    with closing(load_workbook(data/BOOK, read_only=True, data_only=False)) as book:
        rows = book.active.values
        header = next(rows)
        require(len(header) >= 8 and "offer_id" in str(header[0]) and
                str(header[7]).startswith("Кол-во к продаже"), "catalog_columns_changed")
        for row in rows:
            sku = row[0]
            if sku not in required:
                continue
            require(sku not in levels, "ambiguous_stock_sku")
            try:
                value = units(row[7])
            except StockSyncError:
                raise PolicyError("invalid_stock_quantity") from None
            require(value >= 0 and (sku not in LISTINGS or valid_qty(value)), "invalid_stock_quantity")
            levels[sku] = value
    require(required == levels.keys(), "missing_stock_sku")
    for sku in set(LISTINGS) & recipes.keys():
        counts = Counter()
        for component, qty in recipes[sku]:
            require(component not in recipes, "nested_kit")
            counts[component] += qty
        require(counts and levels[sku] == min(levels[c]//n for c,n in counts.items()), "kit_stock_mismatch")
    require(signature == fingerprint(data), "stock_changed_during_read")
    return {sku:levels[sku] for sku in LISTINGS}, signature


def load_state(data):
    state = json.loads((data/STATE).read_bytes())
    require(isinstance(state, dict) and state.get("version") == 1 and
            state.get("baseline_run") == "44795205325" and type(state.get("generation")) is int
            and state["generation"] >= 0 and isinstance(state.get("source_targets"),dict)
            and state["source_targets"].keys() == LISTINGS.keys()
            and all(valid_qty(v) for v in state["source_targets"].values()), "invalid_stock_watermark")
    retry = state.get("unsent_growth", {})
    require(isinstance(retry,dict) and retry.keys() <= LISTINGS.keys()
            and all(valid_qty(v) and v < state["source_targets"][s] for s,v in retry.items()),
            "invalid_stock_watermark")
    return state


def write_json(path, obj, *, exclusive=False):
    raw = json.dumps(obj, sort_keys=True, indent=2).encode() + b"\n"
    with NamedTemporaryFile(dir=path.parent, delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
            if exclusive:
                os.link(tmp, path)
            else:
                os.replace(tmp,path)
        finally:
            tmp.unlink(missing_ok=True)


@contextmanager
def locked(data, plan):
    require(not plan.resolve().is_relative_to(data.resolve()), "plan_must_be_ephemeral")
    runner_temp = os.getenv("RUNNER_TEMP")
    require(not runner_temp or plan.resolve().is_relative_to(Path(runner_temp).resolve()), "plan_outside_runner_temp")
    fd = os.open(data, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PolicyError("stock_writer_busy") from None
        yield
    finally:
        os.close(fd)


def decision(target, previous, before, live):
    if live == target:
        return "exact"
    if live is None or live > target:
        return "cap"
    if target > previous and before == previous and live == previous:
        return "increase"
    return "lower_available_preserved"


def prepare(data, plan, api):
    with locked(data, plan):
        require(not plan.exists() and not Path(str(plan)+".used").exists(), "plan_already_exists")
        qty, sig = targets(data)
        old = load_state(data)
        baseline = old["source_targets"]
        previous = dict(baseline)
        retry = dict(old.get("unsent_growth", {}))
        observed, skipped, status = {}, {}, {}
        for sku in LISTINGS:
            if sku in retry:
                if qty[sku] == baseline[sku]:
                    previous[sku] = retry[sku]
                else:
                    retry.pop(sku)
            try:
                api.verify_listing(sku)
                observed[sku] = api.read(sku)
                status[sku] = {"status":decision(qty[sku],previous[sku],observed[sku],observed[sku]),
                               "target":qty[sku],"available":observed[sku]}
                retry.pop(sku,None)
            except StockError as exc:
                skipped[sku] = exc.code
                status[sku] = {"status":"preflight_skipped","error":exc.code,"target":qty[sku]}
        require(sig == fingerprint(data), "stock_changed_during_prepare")
        next_targets = {s:qty[s] if s in observed else baseline[s] for s in LISTINGS}
        changed = baseline != next_targets or old.get("unsent_growth", {}) != retry
        body = {"listings":dict(LISTINGS), "source":sig, "baseline":baseline,"previous":previous,"targets":qty,
                "observed":observed, "skipped":skipped, "generation":old["generation"]+int(changed),
                "created":time.time(), "nonce":secrets.token_hex(16)}
        new = (dict(old, generation=body["generation"], source_targets=next_targets,
                    unsent_growth=retry, transition=digest(body))
               if changed else old)
        write_json(plan, {"body":body,"watermark":digest(new)}, exclusive=True)
        if changed:
            write_json(data/STATE,new)
        return {"items":status,"state_changed":changed,"ok":True,"preflight_complete":not skipped}


def apply(data, plan, api):
    with locked(data, plan):
        used = Path(str(plan)+".used")
        require(plan.is_file() and not used.exists(), "plan_missing_or_consumed")
        os.link(plan, used)
        plan.unlink()
        envelope = json.loads(used.read_bytes())
        p = envelope["body"]
        state = load_state(data)
        qty, sig = targets(data)
        observed, skipped = p["observed"], p["skipped"]
        require(envelope["watermark"] == digest(state) and p["generation"] == state["generation"]
                and p["targets"] == qty and p["listings"] == dict(LISTINGS) and p["source"] == sig
                and p["previous"].keys() == p["baseline"].keys() == LISTINGS.keys()
                and observed.keys() | skipped.keys() == LISTINGS.keys() and not observed.keys() & skipped.keys()
                and state["source_targets"] == {s:qty[s] if s in observed else p["baseline"][s] for s in LISTINGS}
                and all(valid_qty(v) for v in p["previous"].values())
                and all(v is None or type(v) is int and v >= 0 for v in observed.values()),
                "plan_or_stock_changed")
        if any(p["previous"][s] != qty[s] for s in observed):
            require(digest(p) == state.get("transition"), "growth_proof_invalid")
        items = {}
        unsent = dict(state.get("unsent_growth", {}))
        failed = bool(skipped)
        safe_checkpoint = True
        for sku, target in qty.items():
            result = items[sku] = {"target":target, "put_attempted":False}
            if sku in skipped:
                result.update(status="preflight_skipped", error=StockError(skipped[sku]).code)
                continue
            try:
                require(0 <= time.time()-p["created"] <= PLAN_LIFETIME, "plan_expired")
                require(fingerprint(data)==sig and digest(load_state(data))==envelope["watermark"], "source_changed")
                api.verify_listing(sku)
                live = api.read(sku)
                action = decision(target,p["previous"][sku],p["observed"][sku],live)
                result.update(available=live,status=action)
                if action not in ("increase","cap"):
                    continue
                require(0 <= time.time()-p["created"] <= PLAN_LIFETIME, "plan_expired")
                require(fingerprint(data)==sig and digest(load_state(data))==envelope["watermark"], "source_changed")
                receipt = True
                result["put_attempted"] = True
                try:
                    api.set_quantity(sku,target)
                except StockError as exc:
                    receipt = False
                    result["write_error"] = exc.code
                except Exception:
                    receipt = False
                    result["write_error"] = "client_error"
                # A PUT is never retried. Even an ambiguous transport response gets one strong read.
                try:
                    result["readback"] = api.read(sku)
                except StockError as exc:
                    result["readback_error"] = exc.code
                except Exception:
                    result["readback_error"] = "client_error"
                if not receipt or result.get("readback") != target:
                    result["status"] = "write_unconfirmed"
                    failed = True
                else:
                    result["status"] = "confirmed"
            except StockError as exc:
                result.update(status="listing_check_failed",error=exc.code)
                if not result["put_attempted"] and observed[sku] == p["previous"][sku] < target:
                    unsent[sku] = p["previous"][sku]
                failed = True
            except PolicyError as exc:
                result["status"] = str(exc)
                safe_checkpoint = False
                failed = True
                break
            except Exception:
                result["status"] = "stock_or_storage_error"
                safe_checkpoint = False
                failed = True
                break
        if failed:
            for sku in qty:
                items.setdefault(sku,{"target":qty[sku],"status":"unattempted","put_attempted":False})
        # Only errors strictly before calling set_quantity can return an increase
        # to the next full preflight. Losing this checkpoint cannot cause a replay.
        state_changed = safe_checkpoint and unsent != state.get("unsent_growth", {})
        if state_changed:
            try:
                require(fingerprint(data)==sig and digest(load_state(data))==envelope["watermark"], "source_changed")
                write_json(data/STATE,dict(state, generation=state["generation"]+1, transition=None, unsent_growth=unsent))
            except Exception:
                raise PolicyError("unsent_growth_checkpoint_failed",items) from None
        return {"items":items, "ok":not failed, "state_changed":state_changed,
                "all_exact":all(r["status"] in ("exact","confirmed") for r in items.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["prepare","apply"])
    parser.add_argument("--plan",type=Path,required=True)
    parser.add_argument("--data-dir",type=Path,default=Path(__file__).resolve().parent/"data")
    args = parser.parse_args()
    try:
        client = StockClient()
        result = (prepare if args.mode=="prepare" else apply)(args.data_dir,args.plan,client)
    except PolicyError as exc:
        result = {"ok":False,"error":str(exc),"items":exc.items}
    except StockError as exc:
        result = {"ok":False,"error":exc.code}
    except Exception:
        result = {"ok":False,"error":"invalid_input_or_storage"}
    print(json.dumps(result,sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
