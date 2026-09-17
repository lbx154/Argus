"""Tenant-local durable request/reply mailboxes, with processing acknowledgments.

One SQLite transaction publishes a response and its processing receipt. ACK is
separate, so a crashed consumer retries its durable receipt without repeating
the Manager turn. This store never writes the operator inbox or control ledger.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_PROJECT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MAX_TEXT_BYTES = 16384
MAX_EVIDENCE_REFS = 16


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > MAX_TEXT_BYTES:
        raise ValueError("peer message must be nonempty and at most 16 KiB")
    return value.strip()


def _refs(values: Any) -> list[str]:
    if not isinstance(values, list) or len(values) > MAX_EVIDENCE_REFS or any(
        not isinstance(value, str) or not value.strip() or len(value) > 1024 for value in values
    ):
        raise ValueError("peer evidence must contain at most 16 bounded references")
    return list(dict.fromkeys(value.strip() for value in values))


def _identity(*parts: str) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


class PeerMailbox:
    def __init__(self, global_root: Path | str, project_id: str) -> None:
        self.root = Path(global_root).resolve(strict=True)
        self.project_id = project_id
        self.project_root = self._project(project_id)
        self.path = self.root / "peer-messages.sqlite3"

    def _project(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or not _PROJECT_ID.fullmatch(project_id):
            raise ValueError("recipient must be a known project in this user scope")
        collection = self.root / "projects"
        target = collection / project_id
        if collection.is_symlink() or target.is_symlink() or not target.is_dir():
            raise ValueError("recipient must be a known project in this user scope")
        resolved = target.resolve(strict=True)
        if resolved.parent != collection:
            raise ValueError("peer project escaped the user scope")
        return resolved

    @contextmanager
    def _database(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS messages ("
                       "id TEXT PRIMARY KEY, sender TEXT NOT NULL, recipient TEXT NOT NULL, "
                       "kind TEXT NOT NULL, reply_to TEXT NOT NULL, text TEXT NOT NULL, "
                       "evidence_refs TEXT NOT NULL, parent_call_id TEXT NOT NULL, mission_id TEXT, "
                       "created_at REAL NOT NULL, acknowledged_at REAL NOT NULL DEFAULT 0, "
                       "attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '')")
            db.execute("CREATE INDEX IF NOT EXISTS peer_pending ON messages(recipient,acknowledged_at,next_attempt_at,created_at)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS peer_reply ON messages(reply_to) WHERE kind='reply'")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (message_id TEXT PRIMARY KEY, processed_at REAL NOT NULL, payload TEXT NOT NULL)")
            db.commit()
            with db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def _message(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["message_id"] = result.pop("id")
        result["evidence_refs"] = json.loads(result["evidence_refs"])
        result["authority"] = "peer_advisory"
        return result

    def projects(self, *, limit: int = 100) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for path in (self.root / "projects").iterdir():
            if len(result) >= max(1, min(100, limit)):
                break
            try:
                self._project(path.name)
            except ValueError:
                continue
            if path.name == self.project_id:
                continue
            display_name = path.name
            try:
                with (path / "session.json").open("rb") as handle:
                    raw = handle.read(16385)
                if len(raw) <= 16384:
                    display_name = str(json.loads(raw).get("display_name") or path.name)[:160]
            except (OSError, ValueError, AttributeError):
                pass
            result.append({"project_id": path.name, "display_name": display_name})
        return result

    def send(
        self, recipient: str, text: str, *, parent_call_id: str,
        request_id: str, evidence_refs: list[str] | None = None, mission_id: str | None = None,
    ) -> dict[str, Any]:
        self._project(recipient)
        if recipient == self.project_id:
            raise ValueError("choose a different project for a peer message")
        text, refs = _text(text), _refs(evidence_refs or [])
        if not parent_call_id or not request_id or len(parent_call_id) > 256 or len(request_id) > 256:
            raise ValueError("peer messages require host call and request identities")
        identity = _identity(self.project_id, parent_call_id, request_id)
        with self._database(write=True) as db:
            previous = self._message(db.execute("SELECT * FROM messages WHERE id=?", (identity,)).fetchone())
            if previous:
                if previous["recipient"] != recipient or previous["text"] != text or previous["evidence_refs"] != refs:
                    raise ValueError("peer request identity already names different content")
                return previous
            pending = db.execute("SELECT COUNT(*) FROM messages WHERE recipient=? AND acknowledged_at=0", (recipient,)).fetchone()[0]
            if pending >= 256:
                raise ValueError("recipient peer inbox is full; retry later")
            db.execute("INSERT INTO messages (id,sender,recipient,kind,reply_to,text,evidence_refs,parent_call_id,mission_id,created_at) "
                       "VALUES (?,?,?,'request','',?,?,?,?,?)",
                       (identity, self.project_id, recipient, text, json.dumps(refs), parent_call_id, mission_id, time.time()))
            message = self._message(db.execute("SELECT * FROM messages WHERE id=?", (identity,)).fetchone())
            assert message is not None
            return message

    def pending(self, *, limit: int = 1) -> list[dict[str, Any]]:
        with self._database() as db:
            rows = db.execute("SELECT * FROM messages WHERE recipient=? AND acknowledged_at=0 AND next_attempt_at<=? ORDER BY created_at,id LIMIT ?",
                              (self.project_id, time.time(), max(1, min(10, limit)))).fetchall()
            return [message for row in rows if (message := self._message(row)) is not None]

    def status(self, message_id: str) -> dict[str, Any]:
        with self._database() as db:
            message = self._message(db.execute("SELECT * FROM messages WHERE id=? AND (sender=? OR recipient=?)",
                                               (message_id, self.project_id, self.project_id)).fetchone())
            if message is None:
                raise ValueError("peer message does not belong to this project")
            reply = self._message(db.execute("SELECT * FROM messages WHERE reply_to=?", (message_id,)).fetchone())
            receipt = db.execute("SELECT payload FROM receipts WHERE message_id=?", (message_id,)).fetchone()
            return {"message": message, "reply": reply, "receipt": json.loads(receipt[0]) if receipt else None}

    def processed(self, message_id: str) -> dict[str, Any] | None:
        return self.status(message_id)["receipt"]

    def stage_processed(self, message_id: str, *, response: str, call_id: str = "") -> dict[str, Any]:
        response = _text(response)
        with self._database(write=True) as db:
            message = self._message(db.execute("SELECT * FROM messages WHERE id=? AND recipient=?", (message_id, self.project_id)).fetchone())
            if message is None:
                raise ValueError("only the recipient can process this peer message")
            previous = db.execute("SELECT payload FROM receipts WHERE message_id=?", (message_id,)).fetchone()
            if previous:
                return json.loads(previous[0])
            reply_id = ""
            if message["kind"] == "request":
                self._project(message["sender"])
                reply_id = _identity("reply", message_id)
                db.execute("INSERT OR IGNORE INTO messages (id,sender,recipient,kind,reply_to,text,evidence_refs,parent_call_id,mission_id,created_at) "
                           "VALUES (?,?,?,'reply',?,?,?,?,?,?)",
                           (reply_id, self.project_id, message["sender"], message_id, response, "[]", call_id,
                            message["mission_id"], time.time()))
            receipt = {
                "message_id": message_id, "sender": message["sender"], "recipient": self.project_id,
                "authority": "peer_advisory", "reply_message_id": reply_id,
                "response": response, "manager_call_id": call_id, "processed_at": time.time(),
            }
            db.execute("INSERT INTO receipts VALUES (?,?,?)", (message_id, receipt["processed_at"], json.dumps(receipt, ensure_ascii=False)))
            return receipt

    def acknowledge(self, message_id: str) -> None:
        with self._database(write=True) as db:
            if not db.execute("SELECT 1 FROM receipts WHERE message_id=?", (message_id,)).fetchone():
                raise ValueError("peer messages cannot be acknowledged before processing")
            changed = db.execute("UPDATE messages SET acknowledged_at=? WHERE id=? AND recipient=? AND acknowledged_at=0",
                                 (time.time(), message_id, self.project_id)).rowcount
            if not changed and not db.execute("SELECT 1 FROM messages WHERE id=? AND recipient=? AND acknowledged_at>0", (message_id, self.project_id)).fetchone():
                raise ValueError("peer acknowledgement belongs to another project")

    def retry_later(self, message_id: str, error: str) -> None:
        with self._database(write=True) as db:
            db.execute("UPDATE messages SET attempts=attempts+1,next_attempt_at=?,last_error=? WHERE id=? AND recipient=? AND acknowledged_at=0",
                       (time.time() + 1, str(error)[:1000], message_id, self.project_id))


def mailbox_for_project(project_root: Path | str) -> PeerMailbox:
    original = Path(project_root).absolute()
    if original.is_symlink() or original.parent.name != "projects":
        raise ValueError("peer messaging requires a bound project state directory")
    return PeerMailbox(original.parent.parent, original.name)
