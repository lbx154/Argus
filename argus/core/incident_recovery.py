"""Explicit offline recovery of hash-bound damaged journals; never automatic salvage.

Only fresh library-owned offline copies are supported. No live apply API exists.
The caller must exclusively own the parent namespace; no lock fences old binaries.
No provider execution or risk approval is performed here. Raw bytes, original debt and projection intent are retained.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from pathlib import Path

from .accounting_integrity import AccountingIntegrityError, strict_jsonl, validate_usage_rows
from .safety_io import durable_json, fsync_directory, loads_strict_json

MARKER = "accounting-recovery.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".recovery-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        fsync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def assert_recovery_ready(root):
    from .recovery_paths import RecoveryPaths

    path = root / MARKER
    with RecoveryPaths(root) as paths:
        raw = paths.read(MARKER, optional=True)
    if raw is not None:
        row = loads_strict_json(raw)
        if not isinstance(row, dict) or row.get("phase") != "committed":
            raise AccountingIntegrityError(path, 0, b"", "offline recovery projection pending")


def _audit(directory, event):
    # Append-only audit; intent/marker is authoritative after torn audit writes.
    with (directory / "audit.jsonl").open("ab") as f:
        f.write(json.dumps(event, sort_keys=True, allow_nan=False).encode() + b"\n")
        f.flush()
        os.fsync(f.fileno())
    fsync_directory(directory)


def _project(raw, cuts):
    """Cuts are reviewed byte intervals, never heuristic scanning or skip-invalid."""
    cursor = 0
    result = bytearray()
    for cut in cuts:
        start, length = cut["offset"], cut["length"]
        if type(start) is not int or type(length) is not int or start < cursor or length <= 0:
            raise ValueError("invalid/overlapping reviewed segment")
        segment = raw[start : start + length]
        if len(segment) != length or digest(segment) != cut["sha256"]:
            raise ValueError("segment hash mismatch")
        if start and raw[start - 1 : start] != b"\n":
            raise ValueError("cut must start at a damaged physical line")
        end = raw.find(b"\n", start)
        if end < 0 or start + length > end + 1:
            raise ValueError("cut crosses physical line boundary")
        try:
            loads_strict_json(raw[start:end])
        except (ValueError, UnicodeError):
            pass
        else:
            raise ValueError("cannot archive a valid journal record")
        result.extend(raw[cursor:start])
        cursor = start + length
    result.extend(raw[cursor:])
    return bytes(result)


def _prepare(root: Path, spec: dict):
    """Prepare immutable originals and validated projections; changes no live journal.

    spec binds the full originals, exact damaged byte cuts, the corroborated usage
    call and trusted failed-call event. Provider evidence must be independently
    verified before approving this spec. No unknown cost/tokens/finish is invented.
    """
    from . import cost_control as cc
    from .finalization_intents import _lease_path
    from .usage import UsageRecord, _rewrite_usage_rows, usage_recorded_event

    root = root.resolve()
    project = root / "projects" / spec["project_id"]
    if project.resolve().parent != (root / "projects").resolve():
        raise ValueError("project must be in original global root")
    required = {
        "cost-control.json",
        "cost-control.jsonl",
        "config.json",
        f"projects/{project.name}/usage.jsonl",
        f"projects/{project.name}/events.jsonl",
    }
    if set(spec["originals"]) != required:
        raise ValueError("exact source boundary required")
    originals = {name: (root / name).read_bytes() for name in required}
    if any(digest(raw) != spec["originals"][name] for name, raw in originals.items()):
        raise ValueError("source changed before prepare")
    if (root / MARKER).exists():
        raise ValueError("existing recovery; use apply to resume")
    original_state = loads_strict_json(originals["cost-control.json"])
    state = cc._read_state(root, original_state["updated_at"])
    reservation = next(r for r in state["reservations"] if r["id"] == spec["reservation_id"])
    if (
        reservation["project_id"] != project.name
        or reservation["project_root"] != spec["source_project_root"]
    ):
        raise ValueError("original reservation binding mismatch")
    intent_path = _lease_path(root, reservation["id"])
    if intent_path.exists():
        raise ValueError("not a legacy missing-finalizer obligation")
    usage_name, events_name = (
        f"projects/{project.name}/usage.jsonl",
        f"projects/{project.name}/events.jsonl",
    )
    usage = _project(originals[usage_name], spec["usage_cuts"])
    events = _project(originals[events_name], spec["event_cuts"])
    rows = [loads_strict_json(line) for line in usage.splitlines()]
    event_rows = [loads_strict_json(line) for line in events.splitlines()]
    if not usage.endswith(b"\n") or not events.endswith(b"\n"):
        raise ValueError("unterminated projected journal")
    if any(not isinstance(r, dict) for r in event_rows):
        raise ValueError("nonobject event")
    validate_usage_rows(rows)
    if any(r["project_id"] != project.name for r in rows):
        raise ValueError("usage project binding mismatch")
    if len(spec["usage_cuts"]) != 1 or len(spec["event_cuts"]) != 2:
        raise ValueError("bounded incident requires exactly three damaged segments")
    prefix_cut = spec["usage_cuts"][0]
    prefix = originals[usage_name][
        prefix_cut["offset"] : prefix_cut["offset"] + prefix_cut["length"]
    ]
    if reservation["call_id"].encode() not in prefix or b"\n" in prefix:
        raise ValueError("usage prefix is not bound to retained unknown")
    for cut in spec["event_cuts"]:
        part = originals[events_name][cut["offset"] : cut["offset"] + cut["length"]]
        if part.startswith(b'{"type":"provider.request.completed"'):
            # The truncated engineer error is not its billing source. Keep its
            # already-complete canonical usage and archive all truncated bytes.
            if not any(r["call_id"].encode() in part for r in rows):
                raise ValueError("partial completion lost canonical usage")
        elif not part.startswith(b'{"type":"life.planner.start"'):
            raise ValueError("unrecognized damaged event segment")
    known = [r for r in rows if r["call_id"] == spec["known_call_id"]]
    if len(known) != 1 or any(r["call_id"] == reservation["call_id"] for r in rows):
        raise ValueError("known receipt count or unknown identity conflict")
    expected = usage_recorded_event(UsageRecord.from_jsonable(known[0]))
    matches = [
        e
        for e in event_rows
        if e.get("type") == "usage.recorded" and e.get("call_id") == spec["known_call_id"]
    ]
    if len(matches) != 1 or any(matches[0].get(k) != v for k, v in expected.items()):
        raise ValueError("recovered usage lacks exact canonical event corroboration")
    evidence = spec["provider_evidence"]
    db = root / "provider.db"
    db_bytes = db.read_bytes()
    if digest(db_bytes) != evidence["sqlite_sha256"]:
        raise ValueError("provider evidence changed")
    # Evidence is an explicitly sealed standalone SQLite snapshot, not an active
    # database whose WAL can be ignored. The caller retains its source manifest.
    if Path(str(db) + "-wal").exists():
        raise ValueError("provider snapshot has WAL; use verified sealed snapshot")
    columns = {
        "session_id": "session_id",
        "model": "model",
        "input_tokens": "input_tokens",
        "cached_input_tokens": "cache_read_tokens",
        "cache_write_tokens": "cache_write_tokens",
        "output_tokens": "output_tokens",
        "reasoning_output_tokens": "reasoning_tokens",
        "total_nano_aiu": "total_nano_aiu",
        "created_at": "created_at",
    }
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as con:
        con.row_factory = sqlite3.Row
        models = known[0]["model_usage"]
        if not models or [m["usage_event_id"] for m in models] != evidence["ids"]:
            raise ValueError("provider receipt identities changed")
        for model in models:
            # A sealed table need not retain the provider's PRIMARY KEY. Never
            # pick an arbitrary match, even if repeated rows appear identical.
            found = con.execute(
                "SELECT * FROM assistant_usage_events WHERE id=?", (model["usage_event_id"],)
            ).fetchmany(2)
            if len(found) != 1:
                raise ValueError("provider receipt cardinality must be exactly one")
            receipt = found[0]
            if (
                type(receipt["id"]) is not int
                or receipt["id"] != model["usage_event_id"]
                or any(model[k] != receipt[v] for k, v in columns.items())
            ):
                raise ValueError("provider receipt mismatch")
    if (
        known[0]["provider"] != "copilot"
        or known[0]["total_nano_aiu"] != evidence["nano_aiu"]
        or known[0]["cost_usd"] != evidence["nano_aiu"] / 100_000_000_000
        or sum(m["total_nano_aiu"] for m in models) != evidence["nano_aiu"]
    ):
        raise ValueError("provider charge mismatch")
    completion = [
        e
        for e in event_rows
        if e.get("type") == "agent.io.complete" and e.get("call_id") == reservation["call_id"]
    ]
    starts = [
        e
        for e in event_rows
        if e.get("type") == "provider.request.started"
        and e.get("call_id") == reservation["call_id"]
    ]
    reservations = [
        e
        for e in event_rows
        if e.get("type") == "budget.reservation.created"
        and e.get("reservation_id") == reservation["id"]
    ]
    if (
        len(completion) != 1
        or len(starts) != 1
        or len(reservations) != 1
        or completion[0].get("turn_failed") is not True
        or completion[0].get("thread_id") != spec["unknown_session_id"]
        or not spec["unknown_session_id"]
        or completion[0].get("backend") != reservation["provider"]
        or completion[0].get("model") != reservation["model"]
        or starts[0].get("provider") != reservation["provider"]
        or reservations[0].get("provider") != reservation["provider"]
        or reservations[0].get("model") != reservation["model"]
        or reservations[0].get("amount_usd") != reservation["amount_usd"]
        or not (reservation["created_at"] <= starts[0]["ts"] <= completion[0]["ts"])
        or any(e.get("call_id") != reservation["call_id"] for e in reservations)
        or any(
            e.get("run_label") != reservation["run_label"]
            for e in [completion[0], starts[0], reservations[0]]
        )
    ):
        raise ValueError("missing trusted failed-call binding")
    txid = digest(json.dumps(spec, sort_keys=True, allow_nan=False).encode())
    directory = root / "accounting-recoveries" / txid
    if directory.exists():
        raise ValueError("prepared directory exists; inspect before reuse")
    directory.mkdir(parents=True)
    for name, raw in originals.items():
        p = directory / "original" / name
        atomic_bytes(p, raw)
        p.chmod(0o400)
    segments = {}
    for journal, cuts in ((usage_name, spec["usage_cuts"]), (events_name, spec["event_cuts"])):
        for cut in cuts:
            name = f"segments/{Path(journal).name}-{cut['offset']}.bin"
            raw = originals[journal][cut["offset"] : cut["offset"] + cut["length"]]
            atomic_bytes(directory / name, raw)
            (directory / name).chmod(0o400)
            segments[name] = digest(raw)
    atomic_bytes(directory / "provider-evidence.db", db_bytes)
    (directory / "provider-evidence.db").chmod(0o400)
    durable_json(directory / "spec.json", spec)
    unknown = {
        **reservation,
        "pricing_status": "unknown",
        "blocking": True,
        "reason": "Offline recovery: failed finalizer; billing estimated/unreconciled, no complete receipt",
        "provider_session_id": spec["unknown_session_id"],
        "recovery_provenance": txid,
    }
    # This transfers ownership of the obligation, not evidence of call completion.
    state["reservations"] = [r for r in state["reservations"] if r["id"] != reservation["id"]]
    if any(r["call_id"] == reservation["call_id"] for r in state["unresolved"]):
        raise ValueError("unknown already present")
    state["unresolved"].append(unknown)
    staging = directory / "projection"
    staging.mkdir()
    # Accepted serializer, validation and v2->v3 writer, on empty staging only.
    _rewrite_usage_rows(staging / usage_name, rows)
    strict_jsonl(staging / usage_name, require_call_id=True)
    atomic_bytes(staging / events_name, events)
    original_time = original_state["updated_at"]
    cc._write_state(staging, state, original_time)
    # Recovery must not normalize the accounting day or modify approvals.
    state["day"] = original_state["day"]
    durable_json(staging / "cost-control.json", state)
    cc._read_state(staging, original_time)
    intent = {
        "version": 1,
        "phase": "recovered_unknown",
        "reservation": reservation,
        "receipts": [],
        "errors": [unknown["reason"]],
        "created_at": reservation["created_at"],
        "failure_recorded": True,
        "recovery_provenance": txid,
        "provider_session_id": spec["unknown_session_id"],
    }
    intent_name = str(intent_path.relative_to(root))
    durable_json(staging / intent_name, intent)
    names = [usage_name, events_name, intent_name, "cost-control.json"]
    manifest = {
        "version": 1,
        "id": txid,
        "phase": "prepared",
        "originals": spec["originals"],
        "segments": segments,
        "targets": {n: digest((staging / n).read_bytes()) for n in names},
        "order": names,
        "provider_sha256": digest(db_bytes),
        "reservation": reservation,
        "unknown_session_id": spec["unknown_session_id"],
    }
    durable_json(directory / "manifest.json", manifest)
    _audit(directory, {"event": "prepared", "id": txid, "unknown": reservation["call_id"]})
    # Persist newly-created archive/staging directory entries before the root
    # marker can authorize replacement of any original journal.
    for path in sorted(
        (p for p in directory.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        fsync_directory(path)
    for path in (directory, directory.parent, root):
        fsync_directory(path)
    return {**manifest, "manifest_sha256": digest((directory / "manifest.json").read_bytes())}


def validate_recovered(root, data, pending):
    """A distinct unresolved phase; no lease/completion/not-billed claim."""
    txid = data.get("recovery_provenance", "")
    if (
        not isinstance(txid, str)
        or len(txid) != 64
        or any(c not in "0123456789abcdef" for c in txid)
    ):
        raise ValueError("invalid recovery provenance")
    directory = root / "accounting-recoveries" / txid
    manifest = loads_strict_json((directory / "manifest.json").read_bytes())
    marker = loads_strict_json((root / MARKER).read_bytes())
    original = directory / "original/cost-control.json"
    if (
        marker.get("id") != txid
        or marker.get("phase") != "committed"
        or marker.get("manifest_sha256") != digest((directory / "manifest.json").read_bytes())
        or digest(original.read_bytes()) != manifest["originals"]["cost-control.json"]
        or data["reservation"] != manifest["reservation"]
        or data["reservation"] not in loads_strict_json(original.read_bytes())["reservations"]
    ):
        raise ValueError("uncorroborated recovered obligation")
    if (
        not pending
        or pending.get("recovery_provenance") != txid
        or pending.get("pricing_status") != "unknown"
        or data.get("receipts") != []
        or data.get("failure_recorded") is not True
        or data.get("provider_session_id") != manifest["unknown_session_id"]
        or pending.get("provider_session_id") != manifest["unknown_session_id"]
    ):
        raise ValueError("lost recovered unknown")
    for key in (
        "id",
        "call_id",
        "project_id",
        "project_root",
        "provider",
        "model",
        "run_label",
        "created_at",
    ):
        if pending.get(key) != data["reservation"].get(key):
            raise ValueError("recovered unknown identity changed")
    for key in ("observed_cost_usd", "observed_tokens"):
        if pending.get(key, 0) < data["reservation"].get(key, 0):
            raise ValueError("recovered lower bound lost")


def _apply_to_copy(root: Path, txid: str, *, expected_manifest_sha256: str, fault=None):
    """CAS apply/resume. Each target may be original or projected after a crash.

    Operator exclusion is required throughout. Committed replay is a no-op even
    after later supported acknowledgements. Never rollback later accounting.
    """
    from . import cost_control as cc

    if (
        not isinstance(txid, str)
        or len(txid) != 64
        or any(c not in "0123456789abcdef" for c in txid)
    ):
        raise ValueError("offline authority or transaction identity invalid")
    from .recovery_paths import RecoveryPaths

    with RecoveryPaths(root) as paths:
        prefix = "accounting-recoveries/" + txid + "/"
        manifest_bytes = paths.read(prefix + "manifest.json")
        if digest(manifest_bytes) != expected_manifest_sha256:
            raise ValueError("manifest differs from reviewed hash")
        manifest = loads_strict_json(manifest_bytes)
        spec = loads_strict_json(paths.read(prefix + "spec.json"))
        _validate_spec(spec)
        if digest(json.dumps(spec, sort_keys=True, allow_nan=False).encode()) != txid:
            raise ValueError("archived specification identity mismatch")
        from .finalization_intents import _lease_path

        project = spec["project_id"]
        order = [
            f"projects/{project}/usage.jsonl",
            f"projects/{project}/events.jsonl",
            str(_lease_path(root, spec["reservation_id"]).relative_to(root)),
            "cost-control.json",
        ]
        segments = {}
        for journal, cuts in (
            ("usage.jsonl", spec["usage_cuts"]),
            ("events.jsonl", spec["event_cuts"]),
        ):
            for cut in cuts:
                segments[f"segments/{journal}-{cut['offset']}.bin"] = cut["sha256"]
        if (
            not isinstance(manifest, dict)
            or set(manifest)
            != {
                "version",
                "id",
                "phase",
                "originals",
                "segments",
                "targets",
                "order",
                "provider_sha256",
                "reservation",
                "unknown_session_id",
            }
            or type(manifest["version"]) is not int
            or manifest["version"] != 1
            or manifest["id"] != txid
            or manifest["phase"] != "prepared"
            or manifest["originals"] != spec["originals"]
            or manifest["segments"] != segments
            or manifest["order"] != order
            or set(manifest["targets"]) != set(order)
            or manifest["provider_sha256"] != spec["provider_evidence"]["sqlite_sha256"]
            or manifest["unknown_session_id"] != spec["unknown_session_id"]
            or manifest["reservation"].get("id") != spec["reservation_id"]
            or manifest["reservation"].get("project_id") != project
            or manifest["reservation"].get("project_root") != spec["source_project_root"]
        ):
            raise ValueError("invalid bounded recovery manifest")
        marker_bytes = paths.read(MARKER, optional=True)
        marker = loads_strict_json(marker_bytes) if marker_bytes is not None else None
        identity = {"id": txid, "manifest_sha256": digest(manifest_bytes)}
        if marker is not None and (
            not isinstance(marker, dict)
            or set(marker) != {"id", "manifest_sha256", "phase"}
            or marker.get("phase") not in {"pending", "committed"}
            or any(marker.get(k) != v for k, v in identity.items())
        ):
            raise ValueError("another or modified recovery transaction")
        if digest(paths.read(prefix + "provider-evidence.db")) != manifest["provider_sha256"]:
            raise ValueError("provider archive changed")
        for name, sha in manifest["originals"].items():
            if digest(paths.read(prefix + "original/" + name)) != sha:
                raise ValueError("original archive changed")
        for name, sha in manifest["segments"].items():
            if digest(paths.read(prefix + name)) != sha:
                raise ValueError("damaged segment archive changed")
        projections = {}
        for name, sha in manifest["targets"].items():
            projections[name] = paths.read(prefix + "projection/" + name)
            if digest(projections[name]) != sha:
                raise ValueError("projection changed")
        if set(manifest["order"]) != set(projections) or len(manifest["order"]) != len(projections):
            raise ValueError("invalid projection order")
        # Check every destination before any mutation, including lock creation.
        names = set(manifest["originals"]) | set(projections)
        for name in names | {MARKER, prefix + "audit.jsonl", cc.COST_CONTROL_LOCK_FILE}:
            paths.check(name)
        paths.recheck()
        if marker and marker.get("phase") == "committed":
            return {**identity, "phase": "committed", "duplicate": True}

        def check_sources():
            for name in names:
                raw = paths.read(name, optional=True)
                current = digest(raw) if raw is not None else None
                if name in manifest["originals"]:
                    allowed = {manifest["originals"][name]}
                elif name == order[2]:
                    # The validated order explicitly creates the previously
                    # absent legacy finalizer, never an absent original.
                    allowed = {None}
                else:
                    raise ValueError("unexpected new destination: " + name)
                if marker and name in manifest["targets"]:
                    allowed.add(manifest["targets"][name])
                if current not in allowed:
                    raise ValueError("source changed: " + name)

        check_sources()
        with paths.locked(cc.COST_CONTROL_LOCK_FILE):
            check_sources()
            paths.json(MARKER, {**identity, "phase": "pending"})
            paths.json(prefix + "audit.jsonl", {"event": "apply_started", **identity}, append=True)
            if fault:
                fault("before_projection")
            for name in manifest["order"]:
                paths.write(name, projections[name])
                if fault:
                    fault("after:" + name)
            paths.json(
                prefix + "audit.jsonl", {"event": "projection_durable", **identity}, append=True
            )
            if fault:
                fault("after_projection")
            paths.json(MARKER, {**identity, "phase": "committed"})
            paths.json(prefix + "audit.jsonl", {"event": "committed", **identity}, append=True)
        return {**identity, "phase": "committed", "duplicate": False}


def _validate_spec(spec):
    """Versioned, bounded reviewed cuts; not a generic repair language."""
    keys = {
        "version",
        "project_id",
        "source_project_root",
        "reservation_id",
        "known_call_id",
        "unknown_session_id",
        "originals",
        "usage_cuts",
        "event_cuts",
        "provider_evidence",
    }
    if (
        not isinstance(spec, dict)
        or set(spec) != keys
        or type(spec["version"]) is not int
        or spec["version"] != 1
    ):
        raise ValueError("unsupported recovery specification")
    for key in (
        "project_id",
        "reservation_id",
        "known_call_id",
        "unknown_session_id",
        "source_project_root",
    ):
        if not isinstance(spec[key], str) or not spec[key]:
            raise ValueError("missing recovery identity")
    project = spec["project_id"]
    if project in {".", ".."} or "/" in project or "\\" in project:
        raise ValueError("invalid project identity")
    if not Path(spec["source_project_root"]).is_absolute():
        raise ValueError("source project identity must be absolute (never opened)")
    names = {
        "cost-control.json",
        "cost-control.jsonl",
        "config.json",
        f"projects/{project}/usage.jsonl",
        f"projects/{project}/events.jsonl",
    }

    def sha(value):
        return (
            isinstance(value, str)
            and len(value) == 64
            and all(c in "0123456789abcdef" for c in value)
        )

    if (
        not isinstance(spec["originals"], dict)
        or set(spec["originals"]) != names
        or not all(map(sha, spec["originals"].values()))
    ):
        raise ValueError("invalid original boundary")
    for key, count in (("usage_cuts", 1), ("event_cuts", 2)):
        cuts = spec[key]
        if not isinstance(cuts, list) or len(cuts) != count:
            raise ValueError("unsupported damaged segment count")
        for cut in cuts:
            if (
                not isinstance(cut, dict)
                or set(cut) != {"offset", "length", "sha256"}
                or type(cut["offset"]) is not int
                or cut["offset"] < 0
                or type(cut["length"]) is not int
                or cut["length"] <= 0
                or not sha(cut["sha256"])
            ):
                raise ValueError("invalid reviewed cut")
    evidence = spec["provider_evidence"]
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"sqlite_sha256", "ids", "nano_aiu"}
        or not sha(evidence["sqlite_sha256"])
        or not isinstance(evidence["ids"], list)
        or not evidence["ids"]
        or any(type(i) is not int or i < 0 for i in evidence["ids"])
        or len(set(evidence["ids"])) != len(evidence["ids"])
        or type(evidence["nano_aiu"]) is not int
        or evidence["nano_aiu"] < 0
    ):
        raise ValueError("invalid sealed provider evidence")


class OfflineRecovery:
    """Recovery laboratory, not a live-state installer.

    Use create(parent, originals, provider_sqlite). All inputs are immutable bytes;
    no input file, runtime root or provider database is opened. Parent must be a
    private, exclusively owned namespace. Retain resume_token separately to reopen
    only this generated copy after process death. Recovery of a live root is not
    supported. The token prevents accidental adoption, not hostile same-UID code.
    No claim of legacy-writer containment.
    """

    def __init__(self):
        raise TypeError("use OfflineRecovery.create; existing roots are unsupported")

    @classmethod
    def create(cls, parent: Path, originals: dict[str, bytes], provider_sqlite: bytes):
        import stat
        import threading

        from .recovery_paths import RecoveryPaths

        if os.name != "posix":
            raise NotImplementedError("offline recovery requires POSIX local filesystem semantics")
        parent = Path(parent)
        # Do not create/change permissions of any caller-owned directory.
        if parent.is_symlink():
            raise ValueError("parent must not be a symlink")
        info = parent.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ValueError("parent must be private and owned by this user")
        if not isinstance(originals, dict) or not originals or type(provider_sqlite) is not bytes:
            raise ValueError("sealed byte inputs required")
        for name, raw in originals.items():
            RecoveryPaths.parts(name)
            if (
                type(raw) is not bytes
                or name == "provider.db"
                or name.startswith("accounting-recoveries/")
            ):
                raise ValueError("invalid original input")
        projects = {
            name.split("/")[1]
            for name in originals
            if len(name.split("/")) == 3 and name.startswith("projects/")
        }
        if len(projects) != 1:
            raise ValueError("exact single-project input boundary required")
        project = projects.pop()
        expected = {
            "cost-control.json",
            "cost-control.jsonl",
            "config.json",
            f"projects/{project}/usage.jsonl",
            f"projects/{project}/events.jsonl",
        }
        if set(originals) != expected:
            raise ValueError("exact five original inputs required")
        obj = object.__new__(cls)
        obj._root = Path(tempfile.mkdtemp(prefix="offline-recovery-", dir=parent))
        obj._paths = RecoveryPaths(obj._root)
        obj._guard = threading.Lock()
        obj._closed = False
        # All supported library operations serialize on this pinned lock inode.
        # Other runtime writers do not participate: they are NOT supported here.
        obj._lock = obj._paths.locked("offline-copy.lock")
        obj._lock.__enter__()
        try:
            for name, raw in originals.items():
                obj._paths.write(name, raw)
            obj._paths.write("provider.db", provider_sqlite)
            obj._original_names = set(originals)
            obj.resume_token = secrets.token_hex(32)
            info = obj._root.stat()
            obj._paths.json(
                "offline-copy.json",
                {
                    "version": 1,
                    "token_sha256": digest(obj.resume_token.encode()),
                    "device": info.st_dev,
                    "inode": info.st_ino,
                    "original_names": sorted(originals),
                },
            )
        except BaseException:
            obj.close()
            raise
        fsync_directory(parent)
        return obj

    @classmethod
    def resume(cls, parent: Path, name: str, *, resume_token: str):
        """Reopen a generated offline copy, never adopt an existing runtime root.

        Parent ownership and namespace exclusion remain operating prerequisites.
        A retained token plus device/inode binding prevents accidental reuse at a
        different root; this is not a sandbox against malicious same-UID writers.
        """
        import stat
        import threading

        from .recovery_paths import RecoveryPaths

        if os.name != "posix":
            raise NotImplementedError("offline recovery requires POSIX local filesystem semantics")
        parent = Path(parent)
        if (
            not isinstance(name, str)
            or not name.startswith("offline-recovery-")
            or len(RecoveryPaths.parts(name)) != 1
            or type(resume_token) is not str
        ):
            raise ValueError("invalid offline copy identity")
        for directory in (parent, parent / name):
            info = directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise ValueError("offline namespace must remain private and owned")
        obj = object.__new__(cls)
        obj._root = parent / name
        obj._paths = RecoveryPaths(obj._root)
        try:
            metadata = loads_strict_json(obj._paths.read("offline-copy.json"))
            info = os.fstat(obj._paths.dirs[""])
            if (
                set(metadata) != {"version", "token_sha256", "device", "inode", "original_names"}
                or metadata["version"] != 1
                or metadata["token_sha256"] != digest(resume_token.encode())
                or (metadata["device"], metadata["inode"]) != (info.st_dev, info.st_ino)
            ):
                raise ValueError("offline copy ownership receipt mismatch")
            obj._original_names = set(metadata["original_names"])
            obj._guard = threading.Lock()
            obj._closed = False
            obj.resume_token = resume_token
            obj._lock = obj._paths.locked("offline-copy.lock")
            obj._lock.__enter__()
        except BaseException:
            obj._paths.__exit__()
            raise
        return obj

    @property
    def root(self):
        """Read-only inspection/export location; never configure a runtime here."""
        return self._root

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._guard:
            if not self._closed:
                self._closed = True
                self._lock.__exit__(None, None, None)
                self._paths.__exit__()

    def _check(self):
        if self._closed:
            raise ValueError("offline capability closed; use its retained resume token")
        self._paths.recheck()

    def prepare(self, spec):
        # Defensive deep copy: caller mutation cannot change the prepared intent.
        spec = loads_strict_json(json.dumps(spec, allow_nan=False).encode())
        _validate_spec(spec)
        with self._guard:
            self._check()
            if set(spec["originals"]) != self._original_names:
                raise ValueError("unexpected original files")
            # Validate every existing input through no-follow pinned descriptors
            # before the legacy serializer runs in this exclusively owned copy.
            for name in self._original_names | {"provider.db"}:
                self._paths.read(name)
            archive = self._root / "accounting-recoveries"
            if archive.exists() or archive.is_symlink():
                raise ValueError("copy already prepared or archive namespace changed")
            return _prepare(self._root, spec)

    def apply_to_copy(self, txid, *, expected_manifest_sha256, fault=None):
        with self._guard:
            self._check()
            return _apply_to_copy(
                self._root, txid, expected_manifest_sha256=expected_manifest_sha256, fault=fault
            )

    def read_state(self):
        """Inspect copied state; refuse an incomplete projection."""
        with self._guard:
            self._check()
            assert_recovery_ready(self._root)
            return loads_strict_json(self._paths.read("cost-control.json"))
