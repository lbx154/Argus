"""Durable admission and usage accounting for one trial gateway instance."""
from __future__ import annotations

import hashlib
import math
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from . import GLOBAL_TPM, MAX_CONCURRENCY, TOKEN_LIMIT, TPM_WINDOW_SECONDS, TRIAL_KEY_COUNT


class TrialError(Exception):
    def __init__(self, status: int, code: str, message: str, retry_after: int = 5):
        super().__init__(message)
        self.status, self.code = status, code
        self.retry_after = retry_after


class Store:
    def __init__(self, path: Path, *, clock=time.time, token_limit: int | None = TOKEN_LIMIT,
                 key_limit: int = TRIAL_KEY_COUNT):
        if token_limit is not None and (type(token_limit) is not int or token_limit <= 0):
            raise ValueError("Trial token limit must be a positive integer")
        if type(key_limit) is not int or not 1 <= key_limit <= 100:
            raise ValueError("Trial key limit must be between 1 and 100")
        self.path = path
        self.clock = clock
        self.token_limit = token_limit
        self.key_limit = key_limit
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS trial_keys (
                    key_id TEXT PRIMARY KEY,
                    credential_hash TEXT UNIQUE NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0 CHECK (used >= 0),
                    claim_hash TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS trial_requests (
                    id INTEGER PRIMARY KEY,
                    key_id TEXT NOT NULL REFERENCES trial_keys(key_id),
                    reserved INTEGER NOT NULL,
                    charged INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'active'
                );
                CREATE TABLE IF NOT EXISTS trial_tpm_reservations (
                    request_id INTEGER PRIMARY KEY REFERENCES trial_requests(id),
                    tokens INTEGER NOT NULL,
                    retain_until REAL
                );
                CREATE INDEX IF NOT EXISTS trial_tpm_expiry ON trial_tpm_reservations(retain_until);
                CREATE TABLE IF NOT EXISTS trial_access (
                    key_id TEXT PRIMARY KEY REFERENCES trial_keys(key_id),
                    enabled INTEGER NOT NULL DEFAULT 1,
                    expires_at REAL
                );
                CREATE TABLE IF NOT EXISTS trial_testers (
                    key_id TEXT PRIMARY KEY REFERENCES trial_keys(key_id),
                    tester_id TEXT UNIQUE NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
        path.chmod(0o600)

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    def recover(self):
        # Only called while holding the process-lifetime exclusive gateway lock.
        # Unknown usage stays charged, so killing the server never refunds work.
        with self.transaction() as db:
            # Recover interrupted work without refunding unknown usage.
            db.execute("""INSERT OR IGNORE INTO trial_tpm_reservations(request_id, tokens)
                SELECT id, reserved FROM trial_requests WHERE state='active'""")
            db.execute(
                "UPDATE trial_tpm_reservations SET retain_until=? WHERE retain_until IS NULL",
                (self.clock() + TPM_WINDOW_SECONDS,),
            )
            db.execute("UPDATE trial_requests SET state='interrupted' WHERE state='active'")

    def issue(self, key_id: str, credential: str):
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with self.transaction() as db:
            existing = db.execute("SELECT credential_hash FROM trial_keys WHERE key_id=?", (key_id,)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise ValueError("Existing trial key differs; restore the original master key")
                return
            else:
                count = db.execute("SELECT count(*) FROM trial_keys").fetchone()[0]
                if count >= self.key_limit:
                    raise ValueError(f"All {self.key_limit} trial keys have already been issued")
            db.execute(
                "INSERT OR IGNORE INTO trial_keys(key_id, credential_hash) VALUES (?, ?)",
                (key_id, digest),
            )

    def availability(self) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            issued, available = db.execute(
                "SELECT count(*), coalesce(sum(claim_hash IS NULL), 0) FROM trial_keys"
            ).fetchone()
        return {"total_keys": max(self.key_limit, issued), "issued_keys": issued, "available_keys": available}

    def authenticate(self, credential: str) -> str:
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT key_id FROM trial_keys WHERE credential_hash=?", (digest,)
            ).fetchone()
        if not row:
            raise TrialError(401, "invalid_trial_key", "Invalid trial credential.")
        self.check_access(row[0])
        return row[0]

    def _check_access(self, db, key_id: str):
        row = db.execute("""
            SELECT coalesce(a.enabled,1),a.expires_at FROM trial_keys k
            LEFT JOIN trial_access a ON a.key_id=k.key_id WHERE k.key_id=?
        """, (key_id,)).fetchone()
        if not row:
            raise TrialError(401, "invalid_trial_key", "Invalid trial credential.")
        if not row[0] or (row[1] is not None and row[1] <= self.clock()):
            raise TrialError(403, "trial_access_disabled", "Invitation disabled or expired.")

    def check_access(self, key_id: str):
        with closing(sqlite3.connect(self.path)) as db:
            self._check_access(db, key_id)

    def tester_id(self, key_id: str) -> str:
        with self.transaction() as db:
            self._check_access(db, key_id)
            db.execute(
                "INSERT OR IGNORE INTO trial_testers(key_id,tester_id,created_at) VALUES (?,?,?)",
                (key_id, "tester_" + secrets.token_hex(16), self.clock()),
            )
            return db.execute("SELECT tester_id FROM trial_testers WHERE key_id=?", (key_id,)).fetchone()[0]

    def set_access(self, key_id: str, *, enabled: bool, expires_at: float | None = None):
        if type(enabled) is not bool or (
            expires_at is not None and (
                type(expires_at) not in (float, int) or not math.isfinite(expires_at) or expires_at < 0
            )
        ):
            raise ValueError("Access requires a boolean and a finite expiry timestamp or null")
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM trial_keys WHERE key_id=?", (key_id,)).fetchone():
                raise TrialError(404, "tester_not_found", "Tester not found.")
            db.execute("""
                INSERT INTO trial_access(key_id,enabled,expires_at) VALUES (?,?,?)
                ON CONFLICT(key_id) DO UPDATE SET enabled=excluded.enabled,expires_at=excluded.expires_at
            """, (key_id, int(enabled), expires_at))

    def access_info(self, key_id: str) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("""
                SELECT coalesce(a.enabled,1),a.expires_at,t.tester_id
                FROM trial_keys k LEFT JOIN trial_access a ON a.key_id=k.key_id
                LEFT JOIN trial_testers t ON t.key_id=k.key_id WHERE k.key_id=?
            """, (key_id,)).fetchone()
        if row is None:
            raise TrialError(404, "tester_not_found", "Tester not found.")
        return {
            "tenant_id": key_id, "tester_id": row[2], "enabled": bool(row[0]),
            "expires_at": row[1],
            "access_active": bool(row[0]) and (row[1] is None or row[1] > self.clock()),
        }

    def status(self, key_id: str) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            used = db.execute(
                "SELECT used FROM trial_keys WHERE key_id=?", (key_id,)
            ).fetchone()[0]
            active = db.execute("SELECT count(*) FROM trial_requests WHERE state='active'").fetchone()[0]
            tpm_used = db.execute(
                "SELECT coalesce(sum(tokens),0) FROM trial_tpm_reservations WHERE retain_until IS NULL OR retain_until>?",
                (self.clock(),),
            ).fetchone()[0]
            charges = dict(db.execute(
                "SELECT state,sum(charged) FROM trial_requests WHERE key_id=? GROUP BY state", (key_id,),
            ).fetchall())
        return {
            "token_limit": self.token_limit, "tokens_used": used,
            "tokens_remaining": None if self.token_limit is None else max(0, self.token_limit - used),
            "token_unlimited": self.token_limit is None,
            "active_requests": active, "max_concurrency": MAX_CONCURRENCY,
            "global_tpm_limit": GLOBAL_TPM, "global_tpm_reserved": tpm_used,
            "global_tpm_remaining": max(0, GLOBAL_TPM - tpm_used),
            "tokens_settled": charges.get("settled", 0),
            "tokens_reserved": charges.get("active", 0),
            "tokens_uncertain": sum(v for k, v in charges.items() if k not in {"settled", "active"}),
            "tokens_unattributed": max(0, used - sum(charges.values())),
        }

    def reserve(self, key_id: str, amount: int) -> int:
        if amount <= 0:
            raise ValueError("Reservation must be positive")
        with self.transaction() as db:
            self._check_access(db, key_id)
            now = self.clock()
            db.execute("DELETE FROM trial_tpm_reservations WHERE retain_until<=?", (now,))
            used = db.execute(
                "SELECT used FROM trial_keys WHERE key_id=?", (key_id,)
            ).fetchone()[0]
            if self.token_limit is not None and used + amount > self.token_limit:
                raise TrialError(402, "trial_quota_exceeded", "Insufficient trial tokens for this request.")
            active = db.execute("SELECT count(*) FROM trial_requests WHERE state='active'").fetchone()[0]
            if active >= MAX_CONCURRENCY:
                raise TrialError(429, "trial_busy", "All 10 trial request slots are busy. Try again later.")
            tpm_used = db.execute("SELECT coalesce(sum(tokens),0) FROM trial_tpm_reservations").fetchone()[0]
            if tpm_used + amount > GLOBAL_TPM:
                raise TrialError(
                    429, "trial_tpm_exceeded", "Global trial TPM limit reached. Try again later.",
                    TPM_WINDOW_SECONDS,
                )
            db.execute("UPDATE trial_keys SET used=used+? WHERE key_id=?", (amount, key_id))
            cursor = db.execute(
                "INSERT INTO trial_requests(key_id, reserved, charged) VALUES (?, ?, ?)",
                (key_id, amount, amount),
            )
            request_id = cursor.lastrowid
            db.execute("INSERT INTO trial_tpm_reservations(request_id,tokens) VALUES (?,?)", (request_id, amount))
            return request_id

    def settle(self, request_id: int, actual: int | None):
        if actual is not None and actual < 0:
            raise ValueError("Usage cannot be negative")
        with self.transaction() as db:
            row = db.execute("SELECT * FROM trial_requests WHERE id=?", (request_id,)).fetchone()
            if row["state"] != "active":
                return
            charge = row["reserved"] if actual is None else actual
            db.execute(
                "UPDATE trial_keys SET used=used+? WHERE key_id=?",
                (charge - row["charged"], row["key_id"]),
            )
            db.execute(
                "UPDATE trial_requests SET charged=?, state=? WHERE id=?",
                (charge, "unknown" if actual is None else "settled", request_id),
            )
            # Keep the whole admission estimate through the request and for a
            # full rolling minute after completion. This covers long streams
            # whose output crosses minute boundaries. Only proven zero-use
            # failures refund TPM; lifetime quota still settles to actual usage.
            db.execute(
                "UPDATE trial_tpm_reservations SET tokens=?, retain_until=? WHERE request_id=?",
                (0 if actual == 0 else max(charge, row["reserved"]),
                 self.clock() + TPM_WINDOW_SECONDS, request_id),
            )
