"""Bounded durable operator inbox; transactions never cover classification or delivery.

The SQLite protocol owns new writes. Nonempty JSONL files require an explicit
stopped-writer migration. Known legacy paths become directories, which fence the
old fixed-path writers/readers. This does not fence an old process inventing a
new stage name: any such file makes the new protocol fail closed.

A source ACK retains its envelope until delivery settlement (transient) or
canonical receipt closure (authority). A lease provides fencing, not exactly-once
model execution. Streams are never forgotten: a hard stream limit bounds both
live streams and their closed prefixes, without resurrecting retired identities.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from ..core.file_lock import exclusive_file_lock

PROTOCOL_VERSION = 2
PROTOCOL_DIR = ".inbox-replay"
MAX_STREAMS = 64
MAX_PENDING_MESSAGES = 1024
MAX_PENDING_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 128 * 1024
MAX_DECISION_BYTES = MAX_MESSAGE_BYTES + 16 * 1024
MAX_RECEIPT_BYTES = 16 * 1024
MAX_LEASE_SECONDS = 300.0
LOCK_TIMEOUT_SECONDS = 0.2


class InboxError(RuntimeError):
    """The durable inbox could not complete its requested transition."""


class InboxProtocolError(InboxError):
    """Corrupt, changed, or incompatible queue state; never reset its cursor."""


class InboxMigrationRequired(InboxProtocolError):
    """A nonempty legacy queue needs explicit stopped-writer migration."""


class InboxPartialLine(InboxProtocolError):
    """A legacy partial line must be completed before migration."""


class InboxPressure(InboxError):
    """A fixed queue/stream/payload limit refused admission without losing input."""


class InboxLeaseLost(InboxError):
    """The caller no longer owns this message's unexpired lease."""


class InboxBusy(InboxError):
    """The short queue transaction could not acquire its bounded lock."""


@dataclass(frozen=True)
class InboxClaim:
    identity: dict[str, Any]
    text: str
    source: str
    stage: str
    mission_id: str
    consumer_stage: str
    consumer: str
    owner: str
    lease_until: float
    decision: dict[str, Any] | None
    target_root: str | None
    transient_text: str
    receipt: dict[str, Any] | None
    acknowledged: bool

    @property
    def closed_prefix(self) -> dict[str, Any]:
        return {key: self.identity[key] for key in ("stream", "generation", "sequence")}


def _stage(stage: str) -> str:
    token = re.sub(r"[^a-z0-9_-]+", "-", str(stage or "").strip().lower()).strip("-")
    if len(token) > 80:
        raise InboxPressure("inbox stage exceeds 80 characters")
    return token


def _legacy_path(root: Path, stage: str) -> Path:
    return root / (f"inbox.{stage}.jsonl" if stage else "inbox.jsonl")


def _offset_path(root: Path, stage: str) -> Path:
    return root / (f"inbox.{stage}.offset" if stage else "inbox.offset")


def _sync_dir(root: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _json(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InboxProtocolError("inbox payload must be finite JSON") from exc
    if len(text.encode("utf-8")) > limit:
        raise InboxPressure("inbox payload exceeds its durable bound")
    return text


def _regular(path: Path) -> bool:
    if path.is_symlink():
        raise InboxProtocolError(f"inbox path must not be a symlink: {path.name}")
    return path.is_file()


def _connect(path: Path, *, create: bool = False) -> sqlite3.Connection:
    if path.is_symlink():
        raise InboxProtocolError("inbox database must not be a symlink")
    if not create and not path.is_file():
        raise InboxProtocolError("inbox database is missing; refusing to reset identities")
    connection = sqlite3.connect(path, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA journal_size_limit=0")
    connection.execute("PRAGMA max_page_count=16384")
    os.chmod(path, 0o600)
    return connection


def _schema(db: sqlite3.Connection, queue_id: str) -> None:
    db.execute("PRAGMA page_size=4096")
    db.execute("PRAGMA auto_vacuum=FULL")
    db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE streams (
            stage TEXT PRIMARY KEY, stream TEXT UNIQUE NOT NULL,
            generation INTEGER NOT NULL, next_sequence INTEGER NOT NULL,
            next_offset INTEGER NOT NULL, ack_sequence INTEGER NOT NULL,
            ack_offset INTEGER NOT NULL, last_enqueue REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE messages (
            stage TEXT NOT NULL, sequence INTEGER NOT NULL, digest TEXT NOT NULL,
            text TEXT NOT NULL, raw BLOB NOT NULL, start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL, created REAL NOT NULL,
            mission_id TEXT, consumer_stage TEXT, consumer TEXT,
            owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
            decision TEXT, target_root TEXT, transient_text TEXT NOT NULL DEFAULT '',
            receipt TEXT, accepted INTEGER NOT NULL DEFAULT 0,
            acknowledged INTEGER NOT NULL DEFAULT 0,
            binding_digest TEXT, frozen_digest TEXT, receipt_digest TEXT,
            PRIMARY KEY(stage, sequence)
        );
        INSERT INTO metadata VALUES ('version', '2');
        INSERT INTO metadata VALUES ('status', 'activating');
        COMMIT;
    """)
    db.execute("INSERT INTO metadata VALUES ('queue_id', ?)", (queue_id,))


def _legacy_files(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in root.glob("inbox*.jsonl"):
        if path.name == "inbox.jsonl":
            token = ""
        elif path.name.startswith("inbox."):
            token = path.name[len("inbox."):-len(".jsonl")]
            if not token or _stage(token) != token:
                raise InboxProtocolError("legacy inbox stage is not canonical")
        else:
            continue
        if path.is_symlink():
            raise InboxProtocolError("legacy inbox is a symlink")
        found[token] = path
        if len(found) > MAX_STREAMS:
            raise InboxPressure("inbox stream limit reached")
    return found


def _validate_active(root: Path, db: sqlite3.Connection) -> None:
    meta = dict(db.execute("SELECT key, value FROM metadata"))
    if set(meta) != {"version", "status", "queue_id"} or meta.get("version") != str(PROTOCOL_VERSION) or meta.get("status") != "active":
        raise InboxProtocolError("inbox protocol version or activation is incompatible")
    marker = root / PROTOCOL_DIR / "protocol.json"
    try:
        if marker.is_symlink() or json.loads(marker.read_text()) != {"version": PROTOCOL_VERSION, "queue_id": meta["queue_id"]}:
            raise InboxProtocolError("inbox database generation does not match its durable marker")
    except (OSError, ValueError) as exc:
        raise InboxProtocolError("inbox protocol marker is missing or unreadable") from exc
    stream_rows = db.execute("SELECT * FROM streams LIMIT ?", (MAX_STREAMS + 1,)).fetchall()
    if len(stream_rows) > MAX_STREAMS or any(
        row["generation"] != 1 or row["ack_sequence"] < 0
        or row["next_sequence"] <= row["ack_sequence"]
        or row["next_offset"] < row["ack_offset"] for row in stream_rows
    ):
        raise InboxProtocolError("inbox stream generation or closed prefix is invalid")
    known = {row["stage"] for row in stream_rows}
    for stage, path in _legacy_files(root).items():
        if not path.is_dir():
            raise InboxProtocolError("legacy writer or queue generation changed after activation")
    for stage in known | set(_legacy_files(root)):
        path = _legacy_path(root, stage)
        marker = path / "protocol.json"
        if not path.is_dir() or marker.is_symlink():
            raise InboxProtocolError("inbox legacy fence was removed or replaced")
        try:
            if json.loads(marker.read_text()) != {"version": PROTOCOL_VERSION}:
                raise InboxProtocolError("inbox legacy fence version changed")
        except (OSError, ValueError) as exc:
            raise InboxProtocolError("inbox legacy fence is unreadable") from exc


def _fence(root: Path, stage: str) -> None:
    path = _legacy_path(root, stage)
    if path.exists():
        if not path.is_dir() or path.is_symlink():
            raise InboxProtocolError("refusing to replace an active legacy inbox")
    else:
        path.mkdir()
    marker = path / "protocol.json"
    with marker.open("w", encoding="utf-8") as handle:
        handle.write(_json({"version": PROTOCOL_VERSION}, 100))
        handle.flush()
        os.fsync(handle.fileno())
    _sync_dir(path)
    _sync_dir(root)


def _ensure_stream(db: sqlite3.Connection, root: Path, stage: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM streams WHERE stage=?", (stage,)).fetchone()
    if row is not None:
        return row
    existing = {item[0] for item in db.execute("SELECT stage FROM streams")} | set(_legacy_files(root))
    if stage not in existing and len(existing) >= MAX_STREAMS:
        raise InboxPressure("inbox stream limit reached; closed streams are never evicted")
    _fence(root, stage)
    db.execute("INSERT INTO streams VALUES (?, ?, 1, 1, 0, 0, 0, 0)", (stage, uuid.uuid4().hex))
    return db.execute("SELECT * FROM streams WHERE stage=?", (stage,)).fetchone()


def _timestamp(value: Any) -> float:
    try:
        timestamp = float(value)
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return timestamp if math.isfinite(timestamp) and timestamp > 0 else 0.0


def _insert(db: sqlite3.Connection, root: Path, stage: str, raw: bytes, *, start: int | None = None) -> None:
    if not raw.endswith(b"\n"):
        raise InboxPartialLine("partial inbox line remains unread")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise InboxPressure("inbox message exceeds its durable byte bound")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InboxProtocolError("invalid complete inbox JSON line") from exc
    text = value.get("text") if isinstance(value, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise InboxProtocolError("inbox message must contain nonempty text")
    if not isinstance(value.get("source", ""), str):
        raise InboxProtocolError("inbox message source must be a string")
    count, size = db.execute("SELECT COUNT(*), COALESCE(SUM(length(raw)),0) FROM messages").fetchone()
    if count >= MAX_PENDING_MESSAGES or size + len(raw) > MAX_PENDING_BYTES:
        raise InboxPressure("inbox pending capacity reached; input was not accepted")
    stream = _ensure_stream(db, root, stage)
    offset = stream["next_offset"] if start is None else start
    if offset < stream["next_offset"]:
        raise InboxProtocolError("inbox byte range moved backwards")
    end = offset + len(raw)
    db.execute(
        "INSERT INTO messages (stage,sequence,digest,text,raw,start_offset,end_offset,created) VALUES (?,?,?,?,?,?,?,?)",
        (stage, stream["next_sequence"], hashlib.sha256(raw).hexdigest(), text.strip(), raw, offset, end, time.time()),
    )
    db.execute("UPDATE streams SET next_sequence=next_sequence+1,next_offset=?,last_enqueue=MAX(last_enqueue,?) WHERE stage=?", (end, _timestamp(value.get("ts")), stage))


def _activate(root: Path, *, migrate: bool) -> None:
    root.mkdir(parents=True, exist_ok=True)
    folder = root / PROTOCOL_DIR
    if folder.is_symlink():
        raise InboxProtocolError("inbox protocol directory must not be a symlink")
    folder.mkdir(exist_ok=True, mode=0o700)
    lock_path = folder / "activation.lock"
    if lock_path.is_symlink():
        raise InboxProtocolError("inbox activation lock must not be a symlink")
    with lock_path.open("a+b") as handle:
        try:
            with exclusive_file_lock(handle, timeout_seconds=LOCK_TIMEOUT_SECONDS, lock_name="inbox activation"):
                _activate_locked(root, folder, migrate=migrate)
        except TimeoutError as exc:
            raise InboxBusy("inbox activation is busy") from exc


def _activate_locked(root: Path, folder: Path, *, migrate: bool) -> None:
    database = folder / "queue.sqlite3"
    protocol_marker = folder / "protocol.json"
    if not database.exists() and protocol_marker.exists():
        raise InboxProtocolError("inbox database is missing; refusing to reset identities")
    if database.exists():
        db = _connect(database)
        try:
            try:
                status = db.execute("SELECT value FROM metadata WHERE key='status'").fetchone()
            except sqlite3.DatabaseError as exc:
                raise InboxProtocolError("inbox database is unreadable") from exc
            if status is not None and status[0] == "active":
                _validate_active(root, db)
                return
        finally:
            db.close()
        if not migrate:
            raise InboxMigrationRequired("inbox activation was interrupted; stopped-writer recovery required")
    paths = _legacy_files(root)
    if not migrate and any(path.is_dir() for path in paths.values()):
        raise InboxMigrationRequired("inbox activation was interrupted; explicit recovery required")
    # A prior interrupted activation has private copies, still bounded by the
    # same limits. Reimport happens in one SQLite transaction, never per file.
    archives = {}
    for path in folder.glob("legacy-*.jsonl"):
        token = path.stem[len("legacy-"):]
        if _stage(token) != token or not _regular(path):
            raise InboxProtocolError("legacy migration archive is invalid")
        archives[token] = path
        if len(archives) > MAX_STREAMS:
            raise InboxPressure("legacy archive stream limit reached")
    payloads: dict[str, tuple[bytes, int]] = {}
    for stage in sorted(set(paths) | set(archives)):
        legacy_path = paths.get(stage)
        archive = archives.get(stage)
        if archive is not None and legacy_path is not None and _regular(legacy_path):
            raise InboxProtocolError("legacy writer changed an interrupted migration")
        original = archive or legacy_path
        if original is None or original.is_dir():
            continue
        if original.stat().st_size > MAX_PENDING_BYTES:
            raise InboxPressure("legacy inbox exceeds migration byte bound")
        raw = original.read_bytes()
        if raw and not migrate:
            raise InboxMigrationRequired("nonempty legacy inbox requires writers_stopped=True migration")
        if raw and not raw.endswith(b"\n"):
            raise InboxPartialLine("complete the legacy partial line before stopped-writer migration")
        offset_file = _offset_path(root, stage)
        try:
            offset = int(offset_file.read_text().strip() or "0") if offset_file.exists() else 0
        except (ValueError, OSError) as exc:
            raise InboxProtocolError("legacy inbox offset is unreadable") from exc
        if offset < 0 or offset > len(raw) or (offset and raw[offset - 1:offset] != b"\n"):
            raise InboxProtocolError("legacy inbox was truncated or its offset is not a complete-line boundary")
        payloads[stage] = (raw, offset)
    if len(set(paths) | set(payloads)) > MAX_STREAMS:
        raise InboxPressure("legacy stream limit reached")
    if sum(len(raw) for raw, _offset in payloads.values()) > MAX_PENDING_BYTES:
        raise InboxPressure("legacy inbox aggregate byte bound exceeded")
    for stage, path in paths.items():
        if _regular(path):
            os.replace(path, folder / f"legacy-{stage}.jsonl")
            _sync_dir(folder)
            _sync_dir(root)
    for stage in set(paths) | set(payloads):
        _fence(root, stage)
    if not database.exists():
        db = _connect(database, create=True)
        try:
            _schema(db, uuid.uuid4().hex)
        finally:
            db.close()
    db = _connect(database)
    try:
        queue_id = db.execute("SELECT value FROM metadata WHERE key='queue_id'").fetchone()[0]
        if protocol_marker.exists():
            if json.loads(protocol_marker.read_text()) != {"version": PROTOCOL_VERSION, "queue_id": queue_id}:
                raise InboxProtocolError("inbox activation marker does not match database")
        else:
            with protocol_marker.open("x", encoding="utf-8") as handle:
                handle.write(_json({"version": PROTOCOL_VERSION, "queue_id": queue_id}, 256))
                handle.flush()
                os.fsync(handle.fileno())
            _sync_dir(folder)
        db.execute("BEGIN IMMEDIATE")
        # Only an activation transaction writes messages before active status.
        # An interrupted transaction rolls back in SQLite before this retry.
        if db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]:
            raise InboxProtocolError("inbox activation has unexpected committed messages")
        for stage in sorted(set(paths) | set(payloads)):
            _ensure_stream(db, root, stage)
            raw, offset = payloads.get(stage, (b"", 0))
            sequence = 0
            cursor = 0
            for part in raw.split(b"\n")[:-1]:
                line = part + b"\n"
                sequence += 1
                end = cursor + len(line)
                if end <= offset:
                    try:
                        historical = json.loads(line)
                        timestamp = _timestamp(historical.get("ts")) if isinstance(historical, dict) else 0.0
                    except (ValueError, UnicodeDecodeError):
                        timestamp = 0.0
                    db.execute("UPDATE streams SET last_enqueue=MAX(last_enqueue,?) WHERE stage=?", (timestamp, stage))
                    db.execute("UPDATE streams SET next_sequence=?,next_offset=?,ack_sequence=?,ack_offset=? WHERE stage=?", (sequence + 1, end, sequence, end, stage))
                else:
                    # Invalid complete lines are explicitly rejected, not ACKed
                    # as if they were accepted operator input.
                    _insert(db, root, stage, line, start=cursor)
                cursor = end
        db.execute("UPDATE metadata SET value='active' WHERE key='status'")
        db.commit()
        _sync_dir(folder)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
    for path in folder.glob("legacy-*.jsonl"):
        path.unlink()
    _sync_dir(folder)


def migrate_legacy_inbox(life_dir: Path | str, *, writers_stopped: bool = False) -> None:
    """Explicit offline migration; the caller attests all old writers stopped."""
    if not writers_stopped:
        raise InboxMigrationRequired("legacy migration requires writers_stopped=True")
    _activate(Path(life_dir), migrate=True)


@contextmanager
def _transaction(life_dir: Path | str) -> Iterator[tuple[sqlite3.Connection, Path]]:
    root = Path(life_dir)
    _activate(root, migrate=False)
    db = _connect(root / PROTOCOL_DIR / "queue.sqlite3")
    try:
        db.execute("BEGIN IMMEDIATE")
        _validate_active(root, db)
        yield db, root
        db.commit()
    except sqlite3.OperationalError as exc:
        db.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise InboxBusy("inbox transaction is busy") from exc
        if "full" in str(exc).lower():
            raise InboxPressure("inbox database capacity reached") from exc
        raise InboxProtocolError("inbox transaction failed") from exc
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def enqueue_inbox_message(life_dir: Path | str, text: str, *, source: str, stage: str = "") -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("inbox message must not be empty")
    raw = (_json({"ts": time.time(), "text": text, "source": source}, MAX_MESSAGE_BYTES - 1) + "\n").encode("utf-8")
    with _transaction(life_dir) as (db, root):
        _insert(db, root, _stage(stage), raw)


def _database_exists(root: Path) -> bool:
    folder = root / PROTOCOL_DIR
    database = folder / "queue.sqlite3"
    if folder.is_symlink() or database.is_symlink():
        raise InboxProtocolError("inbox protocol source must not be a symlink")
    if database.exists():
        if not database.is_file():
            raise InboxProtocolError("inbox database is not a regular file")
        return True
    if (folder / "protocol.json").exists():
        raise InboxProtocolError("inbox database is missing; refusing to reset identities")
    return False


@contextmanager
def _readonly(root: Path) -> Iterator[sqlite3.Connection]:
    database = root / PROTOCOL_DIR / "queue.sqlite3"
    uri = "file:" + quote(str(database.resolve()), safe="/") + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=LOCK_TIMEOUT_SECONDS)
    db.row_factory = sqlite3.Row
    try:
        _validate_active(root, db)
        yield db
    except sqlite3.DatabaseError as exc:
        raise InboxProtocolError("inbox read-only source is unavailable") from exc
    finally:
        db.close()


def _legacy_status(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    total_bytes = 0
    for stage, path in sorted(_legacy_files(root).items()):
        if path.is_dir():
            result.append({"stage": stage, "count": 0, "reason": "migration_required", "partial": False})
            continue
        with path.open("rb") as handle:
            raw = handle.read(MAX_PENDING_BYTES + 1)
        total_bytes += len(raw)
        if total_bytes > MAX_PENDING_BYTES:
            raise InboxPressure("legacy inbox exceeds the bounded read size")
        offset_file = _offset_path(root, stage)
        try:
            offset = int(offset_file.read_text().strip() or "0") if offset_file.exists() else 0
        except (ValueError, OSError) as exc:
            raise InboxProtocolError("legacy inbox offset is unreadable") from exc
        if offset < 0 or offset > len(raw) or (offset and raw[offset - 1:offset] != b"\n"):
            raise InboxProtocolError("legacy inbox was truncated or its offset split a line")
        count = 0
        for line in raw[offset:].split(b"\n")[:-1]:
            try:
                value = json.loads(line)
                text = value.get("text") if isinstance(value, dict) else None
                if isinstance(text, str) and text.strip():
                    count += 1
            except (ValueError, UnicodeDecodeError):
                continue
        if raw:
            result.append({"stage": stage, "count": count, "reason": "migration_required", "partial": not raw.endswith(b"\n")})
    return result


def count_durable_inbox_messages(life_dir: Path | str) -> int:
    root = Path(life_dir)
    if not _database_exists(root):
        return sum(item["count"] for item in _legacy_status(root))
    with _readonly(root) as db:
        return int(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])


def _duration(seconds: float) -> float:
    seconds = float(seconds)
    if not math.isfinite(seconds) or seconds <= 0 or seconds > MAX_LEASE_SECONDS:
        raise ValueError(f"inbox lease must be positive and at most {MAX_LEASE_SECONDS:g} seconds")
    return seconds


def _payload_digest(value: Any) -> str:
    # Match canonical OperatorContext effect encoding, including its newline.
    return hashlib.sha256((json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False,
    ) + "\n").encode("utf-8")).hexdigest()


def _raw_fields(row: Any) -> tuple[str, str]:
    raw = bytes(row["raw"])
    if (not raw.endswith(b"\n") or len(raw) > MAX_MESSAGE_BYTES
            or hashlib.sha256(raw).hexdigest() != row["digest"]
            or row["end_offset"] - row["start_offset"] != len(raw)):
        raise InboxProtocolError("inbox complete-line digest or byte range changed")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise InboxProtocolError("inbox original line is not valid JSON") from exc
    text = value.get("text") if isinstance(value, dict) else None
    source = value.get("source", "") if isinstance(value, dict) else None
    if (not isinstance(text, str) or not text.strip() or not isinstance(source, str)
            or text.strip() != row["text"]):
        raise InboxProtocolError("inbox text/source does not agree with its original line")
    return text.strip(), source


def _binding_payload(row: Any) -> dict[str, Any]:
    text, source = _raw_fields(row)
    return {"identity": {"stream": row["stream"], "generation": row["generation"],
                         "sequence": row["sequence"], "digest": row["digest"]},
            "text": text, "source": source, "stage": row["stage"],
            "mission_id": row["mission_id"], "consumer_stage": row["consumer_stage"],
            "consumer": row["consumer"]}


def _frozen_payload(row: Any) -> dict[str, Any]:
    return {"binding_digest": row["binding_digest"], "decision": row["decision"],
            "target_root": row["target_root"], "transient_text": row["transient_text"]}


def _decode_object(raw: Any, limit: int, name: str) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > limit:
        raise InboxProtocolError(f"inbox {name} exceeds its stored payload contract")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("not an object")
        # Reject NaN/Infinity and noncanonical representation in stored state.
        if _json(value, limit) != raw:
            raise ValueError("noncanonical JSON")
    except (ValueError, TypeError) as exc:
        raise InboxProtocolError(f"inbox {name} is not a valid stored object") from exc
    return value


def _validate_plan(plan: dict[str, Any], target: str | None) -> None:
    if target is None:
        return  # A transient envelope may also carry host-defined classification.
    if (set(plan) != {"version", "target_root", "effect"} or type(plan.get("version")) is not int
            or plan.get("version") != 1 or plan.get("target_root") != target
            or not isinstance(plan.get("effect"), dict)):
        raise InboxProtocolError("frozen canonical plan must bind its exact physical target and effect")


def _validate_receipt(receipt: dict[str, Any], identity: dict[str, Any], target: str, plan: dict[str, Any]) -> None:
    if (set(receipt) != {"format", "identity", "target_root", "effect_digest", "revision"}
            or receipt.get("format") != "operator-delivery-receipt-v1"
            or receipt.get("identity") != identity or receipt.get("target_root") != target):
        raise InboxProtocolError("canonical receipt does not bind this inbox identity and target")
    if type(receipt.get("revision")) is not int or receipt["revision"] < 0:
        raise InboxProtocolError("canonical receipt revision must be a nonnegative integer")
    if receipt.get("effect_digest") != _payload_digest(plan):
        raise InboxProtocolError("canonical receipt does not bind the frozen effect")


def _claim(row: sqlite3.Row) -> InboxClaim:
    text, source = _raw_fields(row)
    binding = _binding_payload(row)
    identity = binding["identity"]
    if row["accepted"] not in (0, 1) or row["acknowledged"] not in (0, 1) or (row["acknowledged"] and not row["accepted"]):
        raise InboxProtocolError("inbox acceptance state is inconsistent")
    if row["consumer"] is None:
        if any(row[key] is not None for key in ("mission_id", "consumer_stage", "binding_digest", "owner")):
            raise InboxProtocolError("unclaimed inbox binding is inconsistent")
    else:
        if (not isinstance(row["consumer"], str) or not row["consumer"]
                or not isinstance(row["mission_id"], str) or not isinstance(row["consumer_stage"], str)
                or row["binding_digest"] != _payload_digest(binding)):
            raise InboxProtocolError("inbox original consumer/mission/stage binding changed")
    if (row["acknowledged"] and (row["sequence"] != row["ack_sequence"] or row["end_offset"] != row["ack_offset"])) or (not row["acknowledged"] and row["sequence"] <= row["ack_sequence"]):
        raise InboxProtocolError("inbox envelope and acknowledged source prefix disagree")
    decision = None
    receipt = None
    if row["decision"] is None:
        if (row["consumer"] is None and row["owner"] is not None) or any(
            row[key] is not None for key in ("target_root", "frozen_digest", "receipt", "receipt_digest")
        ) or row["accepted"] or row["acknowledged"] or row["transient_text"]:
            raise InboxProtocolError("unfrozen inbox state contains accepted payload")
    else:
        if row["consumer"] is None or row["frozen_digest"] != _payload_digest(_frozen_payload(row)):
            raise InboxProtocolError("inbox frozen envelope changed")
        decision = _decode_object(row["decision"], MAX_DECISION_BYTES, "decision")
        target = row["target_root"]
        if target is not None and (not isinstance(target, str) or str(Path(target).resolve()) != target):
            raise InboxProtocolError("inbox frozen physical target is invalid")
        _validate_plan(decision, target)
        if not isinstance(row["transient_text"], str) or len(_json(row["transient_text"], MAX_MESSAGE_BYTES).encode()) > MAX_MESSAGE_BYTES:
            raise InboxProtocolError("inbox transient envelope is invalid")
        if target is not None and row["transient_text"]:
            raise InboxProtocolError("canonical inbox cannot carry transient authority")
        if row["receipt"] is not None:
            receipt = _decode_object(row["receipt"], MAX_RECEIPT_BYTES, "receipt")
            if row["receipt_digest"] != _payload_digest(receipt) or target is None or not row["accepted"]:
                raise InboxProtocolError("inbox accepted receipt changed or has no canonical target")
            _validate_receipt(receipt, identity, target, decision)
        elif row["receipt_digest"] is not None or (target is not None and row["accepted"]):
            raise InboxProtocolError("inbox accepted canonical receipt is missing")
    return InboxClaim(
        identity=identity, text=text, source=source, stage=row["stage"], mission_id=row["mission_id"] or "",
        consumer_stage=row["consumer_stage"] or "", consumer=row["consumer"] or "",
        owner=row["owner"] or "", lease_until=row["lease_until"], decision=decision,
        target_root=row["target_root"], transient_text=row["transient_text"], receipt=receipt,
        acknowledged=bool(row["acknowledged"]),
    )


_SELECT = "SELECT messages.*, streams.stream, streams.generation, streams.ack_sequence, streams.ack_offset FROM messages JOIN streams USING(stage)"


def _lease_active(until: float, now: float) -> bool:
    # A backward wall-clock jump must not silently turn a bounded lease into an
    # arbitrarily long owner. The next claimant fences it with a new token.
    return 0 < until - now <= MAX_LEASE_SECONDS


def _owned(db: sqlite3.Connection, claim: InboxClaim) -> sqlite3.Row:
    row = db.execute(_SELECT + " WHERE stage=? AND sequence=?", (claim.stage, claim.identity["sequence"])).fetchone()
    if row is None or _claim(row).identity != claim.identity or row["owner"] != claim.owner or not _lease_active(row["lease_until"], time.time()):
        raise InboxLeaseLost("inbox claim expired or ownership changed")
    return row


def _reload(db: sqlite3.Connection, claim: InboxClaim) -> InboxClaim:
    return _claim(_owned(db, claim))


def claim_inbox_message(
    life_dir: Path | str, *, consumer: str = "engineer", current_stage: str = "",
    mission_id: str = "", lease_seconds: float = 30,
) -> InboxClaim | None:
    duration = _duration(lease_seconds)
    root = Path(life_dir)
    if not _database_exists(root):
        if _legacy_status(root):
            raise InboxMigrationRequired("legacy input requires explicit stopped-writer migration")
        return None
    stage = _stage(current_stage)
    if not consumer or len(consumer) > 80 or len(mission_id) > 512:
        raise ValueError("inbox consumer/mission binding exceeds its bound")
    with _transaction(life_dir) as (db, _root):
        rows = db.execute(_SELECT + " WHERE sequence=(SELECT MIN(m.sequence) FROM messages m WHERE m.stage=messages.stage) ORDER BY acknowledged DESC, created, stage").fetchall()
        now = time.time()
        for row in rows:
            _claim(row)
            if row["owner"] and _lease_active(row["lease_until"], now):
                continue
            canonical_close = bool(row["decision"] is not None and row["target_root"] is not None)
            if row["consumer"] is None:
                if row["stage"] not in {"", stage}:
                    continue
            elif not canonical_close and (row["consumer"], row["mission_id"], row["consumer_stage"]) != (consumer, mission_id, stage):
                continue
            owner = uuid.uuid4().hex
            db.execute("UPDATE messages SET owner=?,lease_until=?,consumer=COALESCE(consumer,?),mission_id=COALESCE(mission_id,?),consumer_stage=COALESCE(consumer_stage,?) WHERE stage=? AND sequence=?", (owner, now + duration, consumer, mission_id, stage, row["stage"], row["sequence"]))
            updated = db.execute(_SELECT + " WHERE stage=? AND sequence=?", (row["stage"], row["sequence"])).fetchone()
            if updated["binding_digest"] is None:
                db.execute("UPDATE messages SET binding_digest=? WHERE stage=? AND sequence=?", (_payload_digest(_binding_payload(updated)), row["stage"], row["sequence"]))
                updated = db.execute(_SELECT + " WHERE stage=? AND sequence=?", (row["stage"], row["sequence"])).fetchone()
            return _claim(updated)
    return None


def renew_inbox_claim(life_dir: Path | str, claim: InboxClaim, *, lease_seconds: float = 30) -> InboxClaim:
    duration = _duration(lease_seconds)
    with _transaction(life_dir) as (db, _root):
        _owned(db, claim)
        db.execute("UPDATE messages SET lease_until=? WHERE stage=? AND sequence=?", (time.time() + duration, claim.stage, claim.identity["sequence"]))
        return _reload(db, claim)


def release_inbox_claim(life_dir: Path | str, claim: InboxClaim) -> None:
    with _transaction(life_dir) as (db, _root):
        _owned(db, claim)
        db.execute("UPDATE messages SET owner=NULL,lease_until=0 WHERE stage=? AND sequence=?", (claim.stage, claim.identity["sequence"]))


def freeze_inbox_decision(
    life_dir: Path | str, claim: InboxClaim, *, decision: dict[str, Any],
    target_root: Path | str | None, transient_text: str = "",
) -> InboxClaim:
    if not isinstance(decision, dict):
        raise InboxProtocolError("frozen inbox decision must be an object")
    encoded = _json(decision, MAX_DECISION_BYTES)
    target = str(Path(target_root).resolve()) if target_root is not None else None
    _validate_plan(decision, target)
    if target is not None and transient_text:
        raise InboxProtocolError("canonical inbox cannot carry transient authority")
    if target is not None and len(target) > 4096:
        raise InboxPressure("inbox target path exceeds its bound")
    _json(transient_text, MAX_MESSAGE_BYTES)
    with _transaction(life_dir) as (db, _root):
        row = _owned(db, claim)
        if row["decision"] is not None:
            if (row["decision"], row["target_root"], row["transient_text"]) != (encoded, target, transient_text):
                raise InboxProtocolError("an inbox decision and its physical target are immutable")
        else:
            frozen = dict(row) | {"decision": encoded, "target_root": target, "transient_text": transient_text}
            db.execute("UPDATE messages SET decision=?,target_root=?,transient_text=?,frozen_digest=? WHERE stage=? AND sequence=?", (encoded, target, transient_text, _payload_digest(_frozen_payload(frozen)), claim.stage, claim.identity["sequence"]))
        return _reload(db, claim)


def accept_inbox_claim(life_dir: Path | str, claim: InboxClaim, *, receipt: dict[str, Any] | None = None) -> InboxClaim:
    encoded = _json(receipt, MAX_RECEIPT_BYTES) if receipt is not None else None
    with _transaction(life_dir) as (db, _root):
        row = _owned(db, claim)
        if row["decision"] is None:
            raise InboxProtocolError("inbox acceptance requires a frozen decision")
        if row["target_root"] is not None and not isinstance(receipt, dict):
            raise InboxProtocolError("canonical inbox acceptance requires a durable receipt")
        if row["target_root"] is not None and receipt is not None:
            _validate_receipt(receipt, claim.identity, row["target_root"], json.loads(row["decision"]))
        if row["target_root"] is None and receipt is not None:
            raise InboxProtocolError("transient inbox acceptance cannot impersonate canonical receipt")
        if row["accepted"] and row["receipt"] != encoded:
            raise InboxProtocolError("inbox acceptance receipt is immutable")
        db.execute("UPDATE messages SET accepted=1,receipt=?,receipt_digest=? WHERE stage=? AND sequence=?", (encoded, _payload_digest(receipt) if receipt is not None else None, claim.stage, claim.identity["sequence"]))
        return _reload(db, claim)


def acknowledge_inbox_claim(life_dir: Path | str, claim: InboxClaim) -> InboxClaim:
    with _transaction(life_dir) as (db, _root):
        row = _owned(db, claim)
        if not row["accepted"]:
            raise InboxProtocolError("inbox cannot ACK before durable acceptance")
        db.execute("UPDATE messages SET acknowledged=1 WHERE stage=? AND sequence=?", (claim.stage, claim.identity["sequence"]))
        db.execute("UPDATE streams SET ack_sequence=?,ack_offset=? WHERE stage=?", (claim.identity["sequence"], row["end_offset"], claim.stage))
        return _reload(db, claim)


def settle_inbox_claim(life_dir: Path | str, claim: InboxClaim, *, canonical_closed: bool = False) -> None:
    with _transaction(life_dir) as (db, _root):
        row = _owned(db, claim)
        if not row["acknowledged"]:
            raise InboxProtocolError("inbox envelope cannot settle before source ACK")
        if row["target_root"] is not None and not canonical_closed:
            raise InboxProtocolError("canonical receipt must close after source ACK before settlement")
        db.execute("DELETE FROM messages WHERE stage=? AND sequence=?", (claim.stage, claim.identity["sequence"]))


def pending_inbox_status(
    life_dir: Path | str, *, consumer: str = "engineer", current_stage: str = "", mission_id: str = "",
) -> list[dict[str, Any]]:
    """Bounded head diagnostics, including envelopes needing their original run.

    A transient for a retired mission is retained, never silently redirected or
    age-pruned. Operators can inspect its original binding here; admitting more
    input eventually fails explicitly at the fixed pressure limits.
    """
    stage = _stage(current_stage)
    root = Path(life_dir)
    if not _database_exists(root):
        return _legacy_status(root)
    with _readonly(root) as db:
        rows = db.execute(_SELECT + " WHERE sequence=(SELECT MIN(m.sequence) FROM messages m WHERE m.stage=messages.stage) ORDER BY stage").fetchall()
        result = []
        for row in rows:
            canonical = row["decision"] is not None and row["target_root"] is not None
            if row["owner"] and _lease_active(row["lease_until"], time.time()):
                reason = "leased"
            elif row["consumer"] is not None and not canonical and (row["consumer"], row["mission_id"], row["consumer_stage"]) != (consumer, mission_id, stage):
                reason = "original_consumer_mission_stage_required"
            elif row["consumer"] is None and row["stage"] not in {"", stage}:
                reason = "queued_stage_required"
            elif row["acknowledged"]:
                reason = "canonical_close_required" if canonical else "transient_settlement_required"
            else:
                reason = "available"
            claim = _claim(row)
            result.append({"identity": claim.identity, "stage": claim.stage, "consumer": claim.consumer,
                           "mission_id": claim.mission_id, "consumer_stage": claim.consumer_stage,
                           "acknowledged": claim.acknowledged, "reason": reason})
        return result


def latest_durable_inbox_timestamp(life_dir: Path | str) -> float | None:
    """Read the persisted last input time without creating/migrating queue state.

    The bounded per-stream timestamp survives envelope settlement. This helper
    deliberately opens SQLite read-only; legacy-only presence remains the
    caller's existing JSONL fallback until an explicit offline migration.
    """
    root = Path(life_dir)
    if not _database_exists(root):
        return None
    with _readonly(root) as db:
        value = _timestamp(db.execute("SELECT MAX(last_enqueue) FROM streams").fetchone()[0])
        return value or None
