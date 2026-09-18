"""Accounting-owned durable call obligations, receipts and finalizer leases.

Execution pause and provider leases live separately in dispatch_safety.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Any

import portalocker

from .accounting_integrity import (
    AccountingIntegrityError,
    durable_json,
    loads_accounting_json,
    strict_jsonl,
    validate_numbers,
    validate_usage_rows,
)


def _json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AccountingIntegrityError(path, 0, b"", "unreadable finalizer state") from exc
    try:
        row = loads_accounting_json(raw)
        if not isinstance(row, dict):
            raise ValueError("not an object")
        return row
    except (ValueError, UnicodeError) as exc:
        raise AccountingIntegrityError(path, 0, raw, "invalid finalizer state") from exc


def _lease_path(root: Path, reservation_id: str) -> Path:
    # Reservation ids are generated internally, not client-selected paths.
    key = hashlib.sha256(reservation_id.encode()).hexdigest()
    return root / "cost-finalizers" / (key + ".json")


def _not_started_receipt(row: dict[str, Any]) -> bool:
    # An explicit denial is not evidence of zero if it carries a bill or usage.
    return (isinstance(row, dict) and row.get("status") == "denied"
            and row.get("pricing_status") == "not_billed" and row.get("cost_usd") == 0
            and not row.get("model_usage")
            and all(row.get(key) in (None, 0) for key in (
                "input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens",
                "reasoning_output_tokens", "total_nano_aiu", "premium_requests",
                "premium_request_cost_usd")))


def _no_charge_evidence(data):
    evidence = data.get("no_charge_evidence")
    if not isinstance(evidence, dict) or evidence.get("call_id") != data["reservation"]["call_id"]:
        return False
    if evidence.get("kind") in {"copilot_preparation", "process_create_enoent"}:
        return evidence.get("producer_checked") is True
    if evidence.get("kind") == "local_startup_receipt":
        from .runner_errors import is_local_startup_refusal
        context = evidence.get("receipt")
        return (isinstance(context, dict) and context.get("call_id") == evidence["call_id"]
                and is_local_startup_refusal(**context))
    return False


class FinalizationIntent:
    """A durable intent and OS lease held from admission through finalization.

    If all later writes fail, losing this lease still exposes the original
    obligation. A live daemon with a failed finalizer cannot hide behind its PID.
    """
    def __init__(self, root: Path, reservation: dict[str, Any]):
        self.path = _lease_path(root, reservation["id"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path.with_suffix(".lock")), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            portalocker.lock(self.fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
            if self.path.exists():
                raise RuntimeError("finalizer intent already exists")
            self.data = {"version": 1, "phase": "open", "reservation": reservation,
                         "receipts": [], "errors": [], "created_at": time.time(),
                         "execution_started": False, "failure_recorded": False}
            durable_json(self.path, self.data)
        except BaseException:
            os.close(self.fd)
            self.fd = -1
            raise

    def mark_execution_started(self) -> None:
        self.assert_releasable()
        self.data["execution_started"] = True
        durable_json(self.path, self.data)

    def assert_releasable(self) -> None:
        # Inspect both durable and retained state: a failed write must not erase
        # in-memory knowledge that execution or finalization may have happened.
        durable = _json(self.path)
        for data in (self.data, durable):
            if (not isinstance(data, dict) or self.fd < 0
                    or data.get("phase") not in {"open", "finalizing"}
                    or data.get("execution_started", False) is not False
                    or data.get("failure_recorded", False) is not False
                    or not isinstance(data.get("receipts"), list)
                    or not isinstance(data.get("errors"), list)
                    or data.get("reservation") != self.data.get("reservation")
                    or any(not _not_started_receipt(r) for r in data.get("receipts", []))
                    or (data.get("errors") and not data.get("receipts"))):
                raise AccountingIntegrityError(self.path, 0, b"", "release lacks not-started lifecycle")

    def assert_no_charge(self, *, proving=False) -> None:
        for data in (self.data, _json(self.path)):
            if (not isinstance(data, dict) or self.fd < 0
                    or data.get("phase") not in {"open", "finalizing"}
                    or data.get("failure_recorded", False) is not False
                    or data.get("reservation") != self.data["reservation"]
                    or any(not _not_started_receipt({**r, "status": "denied"})
                           for r in data.get("receipts", []))
                    or (data.get("errors") and not data.get("receipts"))
                    or (data.get("execution_started") is not False
                        and not proving and not _no_charge_evidence(data))):
                raise AccountingIntegrityError(self.path, 0, b"", "no-charge closure lacks proven lifecycle")

    def prepare(self, *, record: dict[str, Any] | None, error: str = "") -> None:
        self.data["phase"] = "finalizing"
        if record is not None and record not in self.data["receipts"]:
            self.data["receipts"].append(record)
        if error:
            self.data["errors"].append(error)
        durable_json(self.path, self.data)

    def failure(self, error: str) -> None:
        try:
            self.data["phase"] = "failed"
            self.data["failure_recorded"] = True
            self.data["errors"].append(error)
            durable_json(self.path, self.data)
        finally:
            self.detach()

    def complete(self, *, disposition: str) -> None:
        self.data["disposition"] = disposition
        self.data["phase"] = "closed"
        durable_json(self.path, self.data)
        self.detach()

    def detach(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1

    def __del__(self):
        self.detach()


def assert_finalization_integrity(root: Path, reservations: list[dict[str, Any]],
                                  *, state: dict[str, Any] | None = None) -> None:
    """Unknown legacy obligations and abandoned intents gate independently of policy."""
    for row in reservations:
        path = _lease_path(root, str(row.get("id") or ""))
        intent = _json(path)
        bound = (intent or {}).get("reservation", {})
        if (not intent or not isinstance(bound, dict)
                or bound.get("id") != row.get("id")
                or any(bound.get(key) != row.get(key) for key in
                       ("call_id", "project_root", "project_id"))
                or intent.get("phase") == "closed"):
            raise AccountingIntegrityError(root / "cost-control.json", 0, b"",
                                           "reservation lacks verified active finalizer lease")
    # Invocation-local indexes: one validated read per project and one state
    # view, never one full scan per historical intent. Do not cache across calls
    # (missing/replaced ledgers and revisions must remain visible). No usage lock
    # is acquired here; admission retains its pre-read/locked revision check.
    ledgers: dict[Path, dict[str, dict[str, Any]]] = {}
    if state is None:
        from .cost_control import _read_state
        state = _read_state(root, time.time())
    unresolved_index = {(r.get("project_id"), r["call_id"]): r for r in state["unresolved"]}
    reservation_ids = {r["id"] for r in reservations}
    directory = root / "cost-finalizers"
    if not directory.exists():
        return
    seen = {}
    for path in directory.glob("*.json"):
        data = _json(path)
        try:
            if (data is None or type(data.get("version")) is not int or data["version"] != 1
                    or data.get("phase") not in {"open", "finalizing", "failed", "closed"}):
                raise ValueError("invalid finalizer intent")
            for flag in ("execution_started", "failure_recorded"):
                if flag in data and type(data[flag]) is not bool:
                    raise ValueError("invalid finalizer lifecycle flag")
            bound = data.get("reservation")
            if not isinstance(bound, dict):
                raise ValueError("missing finalizer reservation")
            for key in ("id", "call_id"):
                if not isinstance(bound.get(key), str) or not bound[key]:
                    raise ValueError("missing finalizer identity")
            if _lease_path(root, bound["id"]) != path:
                raise ValueError("finalizer filename/identity mismatch")
            for key in ("project_root", "project_id", "provider", "model", "run_label"):
                if not isinstance(bound.get(key), str):
                    raise ValueError("invalid finalizer identity")
            if any(key not in bound or bound[key] is None for key in ("pid", "amount_usd", "created_at")):
                raise ValueError("missing original obligation fields")
            validate_numbers(bound, allow_null=False)
            validate_numbers({"started_at": bound["created_at"]}, allow_null=False)
            if data.get("created_at") is None:
                raise ValueError("missing finalizer timestamp")
            validate_numbers({"started_at": data.get("created_at", -1)})
            if not isinstance(data.get("errors"), list) or any(not isinstance(e, str) for e in data["errors"]):
                raise ValueError("invalid finalizer errors")
            if not isinstance(data.get("receipts"), list):
                raise ValueError("invalid finalizer receipts")
            validate_usage_rows(data["receipts"])
            for receipt in data["receipts"]:
                if receipt["call_id"] != bound["call_id"] or receipt["project_id"] != bound["project_id"]:
                    raise ValueError("finalizer receipt identity mismatch")
            if bound["call_id"] in seen:
                raise ValueError("duplicate finalizer call identity")
            seen[bound["call_id"]] = bound
            if data["phase"] == "open" and bound["id"] not in reservation_ids:
                raise ValueError("open finalizer lost durable reservation")
            if data["phase"] == "closed":
                obligation = data.get("obligation")
                if not isinstance(obligation, dict):
                    raise ValueError("missing closed obligation evidence")
                validate_numbers(obligation, allow_null=False)
                if any(obligation.get(k) != bound.get(k) for k in ("id", "call_id", "project_id", "project_root")):
                    raise ValueError("closed obligation identity mismatch")
                disposition = data.get("disposition")
                if disposition == "released_not_started":
                    evidence = data.get("release_evidence")
                    if (not isinstance(evidence, dict) or evidence.get("kind") != "caller_not_started"
                            or not isinstance(evidence.get("reason"), str) or not evidence["reason"]
                            or evidence.get("lifecycle_checked") is not True
                            or evidence.get("execution_started") is not False
                            or evidence.get("failure_recorded") is not False
                            or type(evidence.get("observed_cost_usd")) not in {int, float}
                            or type(evidence.get("observed_tokens")) is not int
                            or evidence["observed_cost_usd"] != 0 or evidence["observed_tokens"] != 0
                            or obligation.get("observed_cost_usd", 0) != 0 or obligation.get("observed_tokens", 0) != 0):
                        raise ValueError("missing not-started release evidence")
                    if data.get("execution_started") or data.get("failure_recorded"):
                        raise ValueError("release contains executed or failed lifecycle")
                    if any(not _not_started_receipt(r) for r in data["receipts"]):
                        raise ValueError("release contains started receipt")
                    continue
                project = Path(bound["project_root"]) if bound["project_root"] else None
                canonical = None
                if project is not None:
                    if project.resolve().name != bound["project_id"]:
                        raise ValueError("finalizer project root mismatch")
                    project = project.resolve()
                    if project not in ledgers:
                        rows = strict_jsonl(project / "usage.jsonl", require_call_id=True)
                        if any(r["project_id"] != bound["project_id"] for r in rows):
                            raise ValueError("canonical ledger project identity mismatch")
                        ledgers[project] = {r["call_id"]: r for r in rows}
                    canonical = ledgers[project].get(bound["call_id"])
                if disposition == "settled":
                    if canonical is None or not data["receipts"]:
                        raise ValueError("closed settlement missing canonical receipt")
                    current = {k: v for k, v in canonical.items() if k != "accounting_history"}
                    versions = [current, *canonical.get("accounting_history", [])]
                    if any(receipt not in versions for receipt in data["receipts"]):
                        raise ValueError("closed settlement conflicts with canonical provenance")
                elif disposition == "unresolved":
                    unresolved = unresolved_index.get((bound["project_id"], bound["call_id"]))
                    if unresolved is not None and unresolved.get("project_root") != bound["project_root"]:
                        raise ValueError("closed unknown obligation root identity mismatch")
                    if unresolved is None and canonical is None:
                        raise ValueError("closed unknown obligation missing state and receipt")
                else:
                    raise ValueError("missing closed intent disposition")
                if canonical is not None and (canonical["status"] == "denied"
                                               or canonical["pricing_status"] == "not_billed"):
                    if (data.get("failure_recorded") is not False
                            or (data.get("execution_started") is not False and not _no_charge_evidence(data))
                            or obligation.get("observed_cost_usd", 0) != 0
                            or obligation.get("observed_tokens", 0) != 0
                            or any(not _not_started_receipt({**r, "status": "denied"})
                                   for r in data["receipts"])):
                        raise ValueError("closed no-charge settlement lacks consistent lifecycle")
                complete = canonical is not None and (canonical["status"] == "denied"
                    or canonical["pricing_status"] == "not_billed"
                    or (canonical.get("cost_usd") is not None and canonical["pricing_status"] == "priced"))
                if not complete:
                    pending = unresolved_index.get((bound["project_id"], bound["call_id"]))
                    if pending is not None and pending.get("project_root") != bound["project_root"]:
                        raise ValueError("closed incomplete obligation root identity mismatch")
                    if pending is None or any(pending.get(k, 0) < obligation.get(k, 0)
                                              for k in ("observed_cost_usd", "observed_tokens")):
                        raise ValueError("closed incomplete receipt lost retained obligation")
                continue
        except (ValueError, TypeError) as exc:
            raise AccountingIntegrityError(path, 0, b"", str(exc)) from exc
        # Read-only lock open: an absent lock is also unresolved, never repaired
        # by a status/admission check. Per-call lease release precedes no debt deletion.
        try:
            fd = os.open(str(path.with_suffix(".lock")), os.O_RDONLY)
        except FileNotFoundError as exc:
            raise AccountingIntegrityError(path, 0, b"", "missing finalizer lease") from exc
        try:
            try:
                portalocker.lock(fd, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException:
                if data["phase"] in {"open", "finalizing"}:
                    continue
            raise AccountingIntegrityError(path, 0, b"", "unresolved finalizer obligation")
        finally:
            os.close(fd)
