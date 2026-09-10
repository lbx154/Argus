"""Durable admission and usage accounting for one trial gateway instance."""
from __future__ import annotations

import hashlib
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
    def __init__(self, path: Path, *, clock=time.time):
        self.path = path
        self.clock = clock
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
                if count >= TRIAL_KEY_COUNT:
                    raise ValueError("All 10 trial keys have already been issued")
            db.execute(
                "INSERT OR IGNORE INTO trial_keys(key_id, credential_hash) VALUES (?, ?)",
                (key_id, digest),
            )

    def availability(self) -> dict:
        with closing(sqlite3.connect(self.path)) as db:
            issued, available = db.execute(
                "SELECT count(*), coalesce(sum(claim_hash IS NULL), 0) FROM trial_keys"
            ).fetchone()
        return {"total_keys": TRIAL_KEY_COUNT, "issued_keys": issued, "available_keys": available}

    def authenticate(self, credential: str) -> str:
        digest = hashlib.sha256(credential.encode()).hexdigest()
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT key_id FROM trial_keys WHERE credential_hash=?", (digest,)
            ).fetchone()
        if not row:
            raise TrialError(401, "invalid_trial_key", "Invalid trial credential.")
        return row[0]

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
        return {
            "token_limit": TOKEN_LIMIT, "tokens_used": used,
            "tokens_remaining": max(0, TOKEN_LIMIT - used),
            "active_requests": active, "max_concurrency": MAX_CONCURRENCY,
            "global_tpm_limit": GLOBAL_TPM, "global_tpm_reserved": tpm_used,
            "global_tpm_remaining": max(0, GLOBAL_TPM - tpm_used),
        }

    def reserve(self, key_id: str, amount: int) -> int:
        if amount <= 0:
            raise ValueError("Reservation must be positive")
        with self.transaction() as db:
            now = self.clock()
            db.execute("DELETE FROM trial_tpm_reservations WHERE retain_until<=?", (now,))
            used = db.execute(
                "SELECT used FROM trial_keys WHERE key_id=?", (key_id,)
            ).fetchone()[0]
            if used + amount > TOKEN_LIMIT:
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
